"""Native-capability contract against the exact SDK installed in the controller image."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from repository_workflow import RepositoryWorkflow
        from repository_backend import RepositoryBackend
        from repository_ledger import Ledger
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Ledger(Path(self.tmp.name) / 'ledger')
        self.cap = RepositoryWorkflow(RepositoryBackend(ledger=self.ledger))
        self.ctx = SimpleNamespace(deps=SimpleNamespace(session_id='s',workspace_key='a',turn_id='t',
                                   root_run_id='t'), run_id='t',run_step=1)

    def attach(self):
        self.ledger.attach('s','a',dict(repository_id='r'))
        self.ledger.imported('s','a','original')

    async def test_contract_and_plain_mode(self):
        ts = self.cap.get_toolset()
        self.assertEqual(ts.id, 'local_repository_tools_v1')
        self.assertEqual(set(ts.tools), {'list_repositories','open_repository','open_github_issue','search_repository',
            'begin_repository_changes','run_repository_checks','finish_repository_task','repository_task_status'})
        defs = [ToolDefinition(name=n) for n in ['shell','write_file','open_repository','finish_repository_task']]
        self.assertEqual([t.name for t in await self.cap.prepare_tools(self.ctx,defs)],
                         ['shell','write_file','open_repository'])

    async def test_cached_definitions_fail_at_dispatch_when_attached(self):
        self.attach()
        defs = [ToolDefinition(name=n) for n in ['write_file','delegate_task','shell','read_file','write_plan']]
        self.assertEqual([t.name for t in await self.cap.prepare_tools(self.ctx,defs)], ['read_file','write_plan'])
        for tool in defs[:3]:
            with self.subTest(tool=tool.name), self.assertRaises(ToolFailed):
                await self.cap.before_tool_execute(self.ctx, call=ToolCallPart(tool.name,{},tool_call_id=tool.name),
                                                   tool_def=tool,args={'path':'src/lib.rs'})

    async def test_path_confinement(self):
        from repository_backend import workspace_path
        for path in ['../x','/tmp/foo','repository/../outside','repository/.state','Cargo.toml','tests/sub/r.rs']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                workspace_path('write_file',{'path':path})
        self.assertEqual(workspace_path('edit_file',{'path':'src/lib.rs'}),'repository/src/lib.rs')

    async def test_native_agent_registers_repository_plan_and_file_activities(self):
        from moyai.runtime import Runtime
        from moyai.settings import Settings
        from moyai_workspace import SessionWorkspace
        from pydantic_ai.durable_exec.temporal import TemporalDurability
        from pydantic_ai.models.test import TestModel
        from repository_backend import RepositoryBackend
        settings = Settings(data_dir=Path(self.tmp.name)/'state', _env_file=None,
                            workspace_password='ephemeral-sdk-test', default_model='test')
        runtime = Runtime(settings)
        try:
            await runtime.start(models={'test':TestModel()}, workspace=SessionWorkspace(
                RepositoryBackend(self.ledger, plans=runtime.stores.plan)))
            durability = TemporalDurability.from_agent(runtime.agent)
            names = {getattr(fn, '__temporal_activity_definition').name for fn in durability.temporal_activities}
            self.assertIn('agent__moyai__toolset__local_repository_tools_v1__call_tool', names)
            self.assertIn('agent__moyai__capability__local_repository_workflow_v1__admit', names)
            self.assertIn('agent__moyai__capability__local_repository_workflow_v1__model_budget', names)
            self.assertTrue(any('__planning__' in name for name in names))
            self.assertTrue(any('__moyai_artifacts__' in name for name in names))

            # Authorize a native turn and exercise the same storage write/read used by Files.
            from hashlib import sha256
            from moyai.contracts import NewMessage, NewSession
            from moyai.models import User
            from pydantic_ai.workspaces import LocalWorkspaceBackend, Workspace
            from repository_backend import NativeFiles
            from sqlalchemy import select

            async with runtime.database.sessions() as db:
                actor = await db.scalar(select(User).where(User.username == 'admin'))
            chat = await runtime.database.create_session(actor, NewSession(visibility='private'))
            turn = await runtime.database.submit(actor, chat.id, NewMessage(prompt='Test', request_key='files'))
            await runtime.database.claim_next()
            prepared = await runtime.database.prepare(turn.id)
            workspace = Path(self.tmp.name)/'computer'
            target = 'repository-results/changes.patch'
            artifact = workspace/target
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b'expected patch bytes')
            ctx = SimpleNamespace(deps=prepared.scope, workspace=Workspace(LocalWorkspaceBackend(workspace)))
            files = NativeFiles(runtime)
            digest = sha256(artifact.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, 'unconfirmed'):
                await files.deliver(ctx, turn.id, target, digest, False)
            with self.assertRaisesRegex(ValueError, 'differ'):
                await files.deliver(ctx, turn.id, target, '0'*64, True)
            receipt = await files.deliver(ctx, turn.id, target, digest, True)
            self.assertEqual(receipt['sha256'], digest)
            self.assertEqual(receipt['download_url'], '/api/files/' + receipt['id'])
            self.assertEqual(receipt, await files.deliver(ctx, turn.id, target, digest, False))

        finally:
            await runtime.close()
