"""Pinned SDK's activity admission, actual native filesystem writes and authenticated diagnostics."""
from contextlib import closing
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

import httpx
from moyai.api import create_app
from moyai.contracts import NewMessage, NewSession
from moyai.execution import AuthorizedActivity
from moyai.models import User
from moyai.runtime import Runtime
from moyai.settings import Settings
from moyai_workspace import RepositoryFileWorkspace, SessionWorkspace
from pydantic_ai.models.test import TestModel
from pydantic_ai.workspaces import LocalWorkspaceBackend
from pydantic_ai_harness import FileSystem
from sqlalchemy import select
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from temporalio.worker import ExecuteActivityInput

from repository_backend import RepositoryBackend, install_task_status, projected_tree_hash
from repository_ledger import Ledger, RepositoryError
from repository_workflow import identity


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.ledger = Ledger(self.tmp/'ledger.sqlite')
        self.backend = RepositoryBackend(self.ledger, self.tmp/'catalog')
        settings = Settings(data_dir=self.tmp/'state', _env_file=None,
                            workspace_password='ephemeral-sdk-test', default_model='test')
        self.runtime = Runtime(settings)
        self.capability = SessionWorkspace(self.backend)
        await self.runtime.start(models={'test':TestModel()},workspace=self.capability)
        self.addAsyncCleanup(self.runtime.close)
        async with self.runtime.database.sessions() as db:
            self.actor = await db.scalar(select(User).where(User.username == 'admin'))
        self.chat = await self.runtime.database.create_session(self.actor, NewSession(visibility='private'))
        self.turn = await self.activate('first')
        self.scope = (self.chat.id,self.turn.workspace_key)
        self.ledger.attach(*self.scope, {'repository_id':'r','snapshot_id':'s'}, self.actor.id)
        self.workspace = self.tmp/'computer'
        (self.workspace/'repository/src').mkdir(parents=True)
        (self.workspace/'repository/src/lib.rs').write_text('old\n')
        self.manifest = {'files':[{'path':'src/lib.rs','size':4,'sha256':hashlib.sha256(b'old\n').hexdigest()}],
                         'snapshot_id':'s'}
        self.backend.snapshot = lambda binding: (self.manifest,self.tmp)
        self.backend.guest = self.guest
        # Isolate the native write phase. The full baseline-to-verification transition is
        # exercised with signed receipts in test_repository_ledger.py.
        self.ledger.imported(*self.scope, self.tree_hash())
        with closing(sqlite3.connect(self.ledger.path)) as db, db:
            db.execute("UPDATE tasks SET phase='implementing',state=json_set(state,'$.phase','implementing')")
        self.env = ActivityEnvironment()

    def tree_hash(self):
        data = (self.workspace/'repository/src/lib.rs').read_bytes()
        return projected_tree_hash({},'src/lib.rs',data)

    async def guest(self, scope, operation, **fields):
        self.assertEqual(operation,'tree')
        data = (self.workspace/'repository/src/lib.rs').read_bytes()
        return {'ok':True,'snapshot_id':'s','files':[{'path':'src/lib.rs','size':len(data),
            'sha256':hashlib.sha256(data).hexdigest(),'text':data.decode()}], 'tree_hash':self.tree_hash(),
            'protected_unchanged':True}

    async def activate(self, key):
        db = self.runtime.database
        turn = await db.submit(self.actor,self.chat.id,NewMessage(prompt='Test',request_key=key))
        await db.claim_next()
        return (await db.prepare(turn.id)).scope

    async def intercepted(self, name, scope, body):
        self.env.info = replace(self.env.info, activity_type=name)
        next_handler = SimpleNamespace(execute_activity=lambda args: body())
        gate = AuthorizedActivity(next_handler,self.runtime.database)
        return await self.env.run(gate.execute_activity, ExecuteActivityInput(
            fn=body,args=[SimpleNamespace(serialized_run_context={}),scope],executor=None,headers={}))

    async def test_native_plan_refusal_retry_and_repository_gate(self):
        from moyai.agent import WorkflowPlanning
        from pydantic_ai import Agent
        from pydantic_ai.capabilities._ordering import find_capability
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
        from pydantic_ai.models.function import FunctionModel
        from repository_workflow import RepositoryWorkflow

        self.backend.plans = self.runtime.stores.plan
        with closing(sqlite3.connect(self.ledger.path)) as db, db:
            db.execute("""UPDATE tasks SET phase='analyzing',
                state=json_set(state,'$.phase','analyzing','$.baseline_failed',json('true'))""")
        self.ledger.cite(*self.scope,[{'path':'docs/architecture-rules.md'}])
        native = find_capability([self.runtime.agent.root_capability], WorkflowPlanning)
        self.assertIsNotNone(native)
        item = {'content':'Check that retry delays never exceed 30,000 milliseconds.'}
        steps = iter([('write_plan',{'items':[item | {'status':'blocked'}]}),
                      ('begin_repository_changes',{}),
                      ('write_plan',{'items':[item | {'status':'pending'}]}),
                      ('begin_repository_changes',{})])
        async def respond(messages, info):
            action = next(steps,None)
            return ModelResponse(parts=[ToolCallPart(*action) if action else TextPart('Done')])
        agent = Agent(FunctionModel(respond), capabilities=[native,RepositoryWorkflow(self.backend)])
        result = await agent.run('Verify the plan transition.',deps=self.turn,run_id=self.turn.root_run_id)
        returns = [part for msg in result.all_messages() for part in msg.parts if isinstance(part,ToolReturnPart)]
        self.assertEqual([r.outcome for r in returns], ['failed','failed','success','success'])
        self.assertIn('depends on nothing',str(returns[0].content))
        self.assertIn('pending',str(returns[0].content))
        self.assertIn('No native plan',str(returns[1].content))
        self.assertIn('begin_repository_changes',str(returns[2].content))
        self.assertEqual(returns[3].content['phase'],'implementing')
        saved = await self.runtime.stores.plan(self.chat.id).get_items()
        self.assertEqual(self.ledger.status(*self.scope)['plan'],[i.model_dump(mode='json') for i in saved])

    async def test_native_edit_rejects_unreadable_escaped_tree_before_writing(self):
        from repository_backend import canonical, fingerprint
        folder = self.workspace/'repository'
        data = ('é' * (256 << 10)).encode()
        for index in range(10):
            (folder/f'protected{index}.txt').write_bytes(data)
        def read_tree():
            entries = []
            for path in sorted(p for p in folder.rglob('*') if p.is_file()):
                raw = path.read_bytes()
                entries.append(dict(path=path.relative_to(folder).as_posix(),size=len(raw),
                                    sha256=hashlib.sha256(raw).hexdigest(),text=raw.decode()))
            rows = [{k:e[k] for k in ('path','sha256','size')} for e in entries]
            return rows, dict(ok=True,files=entries,snapshot_id='s',protected_unchanged=True,
                              tree_hash=fingerprint(rows))
        self.manifest['files'], before = read_tree()
        async def bounded_guest(scope, op):
            self.assertEqual(op,'tree')
            _, reply = read_tree()
            if len(canonical(reply)) > (16 << 20):
                raise RepositoryError('Guest tree wire limit')
            return reply
        self.backend.guest = bounded_guest
        ctx = SimpleNamespace(deps=self.turn,run_id=self.turn.turn_id,tool_call_id='wire',tool_name='edit_file')
        operation = identity(ctx,'wire')
        await self.capability.admit(*self.scope,True,operation,'edit_file','src/lib.rs','wire',5,self.turn.turn_id)
        native = FileSystem().get_toolset()
        workspace = RepositoryFileWorkspace(LocalWorkspaceBackend(self.workspace),ctx,self.backend)
        with self.assertRaisesRegex(Exception,'wire'):
            await native.edit_file('repository/src/lib.rs','old',data.decode()[:-1],workspace=workspace)
        self.assertEqual((folder/'src/lib.rs').read_bytes(),b'old\n')
        self.assertEqual(read_tree()[1]['tree_hash'],before['tree_hash'])
        self.assertEqual(self.ledger.status(*self.scope)['write_dispatch'],'reserved')

    async def test_interrupted_activity_reconciles_without_replaying_after_stop(self):
        old = self.turn
        ctx = SimpleNamespace(deps=old,run_id=old.turn_id,tool_call_id='write1',tool_name='write_file')
        op = identity(ctx,'write1')
        admit = 'agent__moyai__capability__local_repository_workflow_v1__admit'
        async def reserve():
            return await self.capability.admit(*self.scope,True,op,'write_file','src/lib.rs',
                                               'payload',8,old.turn_id)
        self.assertEqual(await self.intercepted(admit,old,reserve),{'path':'repository/src/lib.rs'})
        await self.runtime.database.stop(self.actor,self.chat.id)
        with self.assertRaises(ApplicationError):
            await self.intercepted('agent__moyai__capability__local_repository_workflow_v1__write_settlement',
                old, lambda: self.capability.write_settlement(*self.scope,op,'refused'))
        await self.runtime.database.stopped(old.turn_id)
        fresh = await self.activate('fresh')
        report = await self.intercepted('agent__moyai__toolset__local_repository_tools_v1__call_tool',
            fresh, lambda: self.backend.status(self.scope,fresh.turn_id))
        self.assertEqual((report['phase'],report['write_outcome']),('implementing','not_dispatched'))
        workspace = RepositoryFileWorkspace(LocalWorkspaceBackend(self.workspace),ctx,self.backend)
        with self.assertRaises(RepositoryError):
            await workspace.write_bytes('repository/src/lib.rs',b'late')
        self.assertEqual((self.workspace/'repository/src/lib.rs').read_text(),'old\n')

    async def test_native_filesystem_preflights_exact_result_and_records_observed_postimage(self):
        ctx = SimpleNamespace(deps=self.turn,run_id=self.turn.turn_id,tool_call_id='edit1',tool_name='edit_file')
        workspace = RepositoryFileWorkspace(LocalWorkspaceBackend(self.workspace),ctx,self.backend)
        op = identity(ctx,'edit1')
        old = b'x' * ((512 << 10) - 2) + b'zz'
        (self.workspace/'repository/src/lib.rs').write_bytes(old)
        admitted = await self.capability.admit(*self.scope,True,op,'edit_file','src/lib.rs','payload',16,self.turn.turn_id)
        self.assertEqual(admitted,{'path':'repository/src/lib.rs'})
        native = FileSystem().get_toolset()
        with self.assertRaisesRegex(Exception, 'size limit'):
            await native.edit_file('repository/src/lib.rs','zz','zzXX',workspace=workspace)
        self.assertEqual((self.workspace/'repository/src/lib.rs').read_bytes(),old)
        await self.capability.write_settlement(*self.scope,op,'refused')
        ctx.tool_call_id = 'repair'
        op = identity(ctx,'repair')
        await self.capability.admit(*self.scope,True,op,'edit_file','src/lib.rs','next',8,self.turn.turn_id)
        await native.edit_file('repository/src/lib.rs','zz','yy',workspace=workspace)
        task = self.ledger.status(*self.scope)
        self.assertEqual((task['phase'],task['write_outcome']),('implementing','observed_expected_tree'))
        self.assertEqual(task['write_after'],self.tree_hash())

    async def test_terminal_status_route_uses_native_auth_and_no_execution_budget(self):
        app = create_app(self.runtime)
        install_task_status(app,self.runtime,self.backend)
        self.ledger.limits['tool'] = 0
        with self.assertRaises(RepositoryError):
            self.ledger.charge(*self.scope,'tool','exhausted')
        route = '/api/sessions/' + self.chat.id + '/repository-task'
        token, _ = self.runtime.security.issue_session(self.actor.id,self.actor.auth_version)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as client:
            self.assertEqual((await client.get(route)).status_code,401)
            client.cookies.set('moyai_session',token)
            response = await client.get(route)
            self.assertEqual(response.status_code,200,response.text)
            result = response.json()
            self.assertEqual(result['phase'],'failed')
            self.assertIn('receipts',result)
            self.assertNotIn('authority',result)
            self.assertNotIn('receipt_key',response.text)
            self.assertEqual((await client.get('/api/sessions/other/repository-task')).status_code,404)
            async with self.runtime.database.sessions.begin() as db:
                other = User(username='reader',display_name='Reader',password='unused',role='member')
                db.add(other)
                await db.flush()
                cookie, _ = self.runtime.security.issue_session(other.id, other.auth_version)
            client.cookies.set('moyai_session', cookie)
            self.assertIn((await client.get(route)).status_code, (403,404))

    async def test_active_status_calls_exhaust_the_tool_budget(self):
        self.ledger.limits['tool'] = 1
        ctx = self.turn
        first = await self.capability.admit(*self.scope,True,'s1','repository_task_status','.','',0,ctx.turn_id)
        second = await self.capability.admit(*self.scope,True,'s2','repository_task_status','.','',0,ctx.turn_id)
        self.assertEqual(first,{})
        self.assertIn('budget exhausted',second['denied'])

    async def test_native_workspace_activity_receives_exact_file_tool_identity(self):
        from dataclasses import replace
        from unittest.mock import patch
        from pydantic_ai import RunContext
        from pydantic_ai.durable_exec._workspace import DurableWorkspace
        from pydantic_ai.durable_exec.temporal import TemporalDurability
        from pydantic_ai.durable_exec.temporal._transports import _WorkspaceCallTransport
        from pydantic_ai.messages import ToolCallPart
        from pydantic_ai.tools import ToolDefinition
        from pydantic_ai.usage import RunUsage
        from pydantic_ai.workspaces import Workspace

        bound = TemporalDurability.from_agent(self.runtime.agent)
        name = 'agent__moyai__capability__workspace__call'
        native_activity = next(fn for fn in bound.temporal_activities
                               if getattr(fn, '__temporal_activity_definition').name == name)
        transport = _WorkspaceCallTransport(bound)
        native = LocalWorkspaceBackend(self.workspace)
        base = RunContext(deps=self.turn, run_id=self.turn.turn_id, model=TestModel(),
                          usage=RunUsage(), workspace=Workspace(native))
        observed = []
        async def send(params):
            wire, deps = transport.dump(params)
            observed.append((wire.call.method,wire.serialized_run_context['tool_call_id']))
            return await self.intercepted(name,deps,lambda: native_activity(wire,deps))
        workspace = DurableWorkspace(Workspace(native),ctx=base,durability=SimpleNamespace(
            in_durable_context=True,_call_workspace=send))
        ctx = replace(base, workspace=workspace, tool_name='write_file', tool_call_id='actual-sdk-call')
        call = ToolCallPart('write_file',{'path':'src/lib.rs','content':'updated'},tool_call_id=ctx.tool_call_id)
        await self.capability.admit(*self.scope,True,identity(ctx,ctx.tool_call_id),'write_file',
                                    'src/lib.rs','fingerprint',7,self.turn.turn_id)
        async def run(_args):
            await workspace.write_bytes('repository/src/lib.rs',b'updated')
        def local_workspace(_self, activity_ctx, *, ref):
            return RepositoryFileWorkspace(native,activity_ctx,self.backend)
        with patch.object(SessionWorkspace, 'get_workspace', local_workspace):
            await self.capability.wrap_tool_execute(ctx,call=call,tool_def=ToolDefinition(name='write_file'),
                                                   args=call.args_as_dict(),handler=run)
        self.assertIn(('write_bytes','actual-sdk-call'),observed)
        self.assertEqual((self.workspace/'repository/src/lib.rs').read_text(),'updated')
        self.assertEqual(self.ledger.status(*self.scope)['write_outcome'],'observed_expected_tree')
