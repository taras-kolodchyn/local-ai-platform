"""Native approval history and its interaction with external-action receipts."""
from pathlib import Path
import tempfile
import unittest

from moyai.connections import ConnectionTools
from moyai.contracts import NewMessage, NewSession
from moyai.effects import EffectReceipts
from moyai.models import Connection, Effect, User
from moyai.runtime import Runtime
from moyai.settings import Settings
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy import select


class EffectApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        runtime = Runtime(Settings(data_dir=Path(directory), _env_file=None,
                                   workspace_password='isolated-test-password', default_model='test'))
        self.addAsyncCleanup(runtime.close)
        self.dispatched = []
        toolset = FunctionToolset()

        @toolset.tool_plain
        async def github_fixture_create_pull_request():
            self.dispatched.append('pull')
            return 'pull created'

        @toolset.tool_plain
        async def github_fixture_create_branch():
            self.dispatched.append('branch')
            return 'branch created'

        def respond(messages, _info):
            returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
            if any(part.tool_call_id == 'branch' for part in returns):
                return ModelResponse(parts=[TextPart('Branch request processed.')])
            if any(part.tool_call_id == 'pull' and part.outcome == 'denied' for part in returns):
                return ModelResponse(parts=[ToolCallPart('github_fixture_create_branch', {}, tool_call_id='branch')])
            return ModelResponse(parts=[ToolCallPart('github_fixture_create_pull_request', {}, tool_call_id='pull')])

        model = FunctionModel(respond)
        await runtime.start(models={'test': model})
        self.database = runtime.database
        async with self.database.sessions.begin() as db:
            actor = await db.scalar(select(User).where(User.username == 'admin'))
            db.add(Connection(id='fixture', owner_id=actor.id, kind='github', name='Fixture',
                              credential=runtime.security.seal('fixture'), read_only=False))
        session = await self.database.create_session(actor, NewSession(visibility='private'))
        turn = await self.database.submit(actor, session.id, NewMessage(
            prompt='Prepare a change.', request_key='first', connections=['fixture']))
        await self.database.claim_next()
        self.scope = (await self.database.prepare(turn.id)).scope
        self.receipts = EffectReceipts(self.database)
        # Discovery can expose a deferred connection operation as a normal
        # function. The provider wrapper then requests approval at call time.
        connection = ConnectionTools(toolset.approval_required(), id='moyai_connections')
        self.runner = Agent(model, output_type=[str, DeferredToolRequests],
                            capabilities=[self.receipts, connection])

    async def effects(self):
        async with self.database.sessions() as db:
            return [(effect.tool_name, effect.state) for effect in await db.scalars(select(Effect))]

    async def request_branch(self):
        result = await self.runner.run('Prepare a change.', deps=self.scope)
        self.assertIsInstance(result.output, DeferredToolRequests)
        self.assertEqual([call.tool_call_id for call in result.output.approvals], ['pull'])
        return await self.resume(result, 'pull', False)

    async def resume(self, result, call_id, approved):
        return await self.runner.run(deps=self.scope, message_history=result.all_messages(),
                                     deferred_tool_results=DeferredToolResults(approvals={call_id: approved}))

    async def test_decline_is_recorded_and_next_approved_write_dispatches(self):
        result = await self.request_branch()
        self.assertIsInstance(result.output, DeferredToolRequests)
        self.assertEqual([call.tool_call_id for call in result.output.approvals], ['branch'])
        self.assertTrue(any(isinstance(part, ToolReturnPart) and part.tool_call_id == 'pull' and part.outcome == 'denied'
                            for message in result.all_messages() for part in message.parts))
        self.assertEqual(await self.effects(), [])
        self.assertEqual(self.dispatched, [])

        result = await self.resume(result, 'branch', True)
        self.assertEqual(result.output, 'Branch request processed.')
        self.assertEqual(self.dispatched, ['branch'])
        self.assertEqual(await self.effects(), [('github_fixture_create_branch', 'confirmed')])

    async def test_approval_preserves_an_unrelated_unknown_outcome(self):
        result = await self.request_branch()
        self.assertIsInstance(result.output, DeferredToolRequests)
        await self.receipts.reserve(self.scope, 'unknown', 'github_fixture_push_files', {})
        result = await self.resume(result, 'branch', True)
        self.assertTrue(any(isinstance(part, ToolReturnPart) and part.tool_call_id == 'branch'
                            and part.outcome == 'failed' and 'unconfirmed outcome' in part.content
                            for message in result.all_messages() for part in message.parts))
        self.assertEqual(self.dispatched, [])
        self.assertEqual(await self.effects(), [('github_fixture_push_files', 'pending')])
