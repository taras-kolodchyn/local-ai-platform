"""Check the arguments that enter the native confirmation boundary."""
from types import SimpleNamespace as Values
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from moyai.connection_catalog import ConnectionToolSearch, connection_search
from moyai.connections import ConnectionTools
from moyai.contracts import Grant, RunScope
from moyai_workspace import SessionWorkspace
from repository_workflow import RepositoryWorkflow
from staged_files import EDIT_PREFIX
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition
from dataclasses import dataclass
import shlex
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.toolsets import FunctionToolset


class StagedPublicationTests(IsolatedAsyncioTestCase):
    async def test_ready_index_finishes_local_step_without_another_tool_call(self):
        cap = SessionWorkspace()
        cap.authorize_issue_response = AsyncMock()
        cap.checkout_ready = AsyncMock(side_effect=[False, True])
        base = 'a' * 40
        ctx = Values(prompt=f'{EDIT_PREFIX}{base}\nPrepare the local changes.', run_id='r',
                     deps=Values(session_id='s', workspace_key='w', root_run_id='r',
                                 visibility='private', surface='web'))
        request, handler = Values(), AsyncMock()
        with patch.object(RepositoryWorkflow, 'wrap_model_request', new_callable=AsyncMock) as fallback:
            async def route():
                return await cap.wrap_model_request(ctx, request_context=request, handler=handler)

            self.assertIs(await route(), fallback.return_value)
            result = await route()
            self.assertEqual(result.finish_reason, 'stop')
            self.assertFalse(result.tool_calls)
            self.assertIn('ready', result.text)
            self.assertEqual(fallback.await_count, 1)
            self.assertEqual(cap.checkout_ready.await_count, 2)
            cap.checkout_ready.assert_awaited_with('s', 'w', base)
            cap.authorize_issue_response.assert_awaited_with(ctx, request)

            ctx.run_id = 'child'
            self.assertIs(await route(), fallback.return_value)
            ctx.run_id = 'r'
            ctx.prompt = 'Review this issue.\n' + ctx.prompt
            self.assertIs(await route(), fallback.return_value)
            self.assertEqual(cap.checkout_ready.await_count, 2)

    async def test_repair_selection_dispatches_only_validated_local_paths(self):
        @dataclass
        class Request:
            messages: list
            model_request_parameters: ModelRequestParameters
            model_settings: dict

        cap = SessionWorkspace()
        request = Request([], ModelRequestParameters(function_tools=[ToolDefinition(name='shell')]), {})
        base = 'b' * 40
        ctx = Values(prompt=f'{EDIT_PREFIX}{base}\nPrepare the existing edits for issue #4',
                     deps=Values(turn_id='repair'))
        state = {'status': 'stage_changes', 'paths': ['source.py', 'new file.py'], 'staged': ['source.py']}
        choice = ['source.py', 'new file.py']

        async def select(wire):
            tool, = wire.model_request_parameters.function_tools
            self.assertEqual(tool.name, 'select_checkout_paths')
            return ModelResponse(parts=[ToolCallPart(tool.name, {'paths': choice})])

        response = await cap.repair_response(ctx, request, select, base, state, ctx.prompt)
        call, = response.tool_calls
        self.assertEqual(call.tool_name, 'shell')
        self.assertEqual(shlex.split(call.args['command'])[-4:], ['--stage', base, *choice])
        choice[:] = ['new file.py']
        self.assertFalse((await cap.repair_response(ctx, request, select, base, state, ctx.prompt)).tool_calls)
        choice[:] = ['source.py', 'unlisted']
        self.assertFalse((await cap.repair_response(ctx, request, select, base, state, ctx.prompt)).tool_calls)
        request.messages = [ModelRequest(parts=[ToolReturnPart('shell', 'done', call.tool_call_id)])]
        self.assertFalse((await cap.repair_response(ctx, request, AsyncMock(), base, state, ctx.prompt)).tool_calls)

    async def test_record_tests_dispatches_the_pinned_suite_once(self):
        cap = SessionWorkspace()
        ctx = Values(deps=Values(turn_id='repair'))
        request = Values(messages=[], model_request_parameters=Values(function_tools=[ToolDefinition(name='shell')]))
        handler = AsyncMock()
        base = 'a' * 40
        prompt = 'The original issue requirements are data: {"body":"Run `python3 -B -m unittest -v`."}'
        async def route():
            return await cap.repair_response(ctx, request, handler, base, {'status': 'record_tests'}, prompt)
        call, = (await route()).tool_calls
        self.assertEqual(shlex.split(call.args['command'])[-3:], ['--test', base, 'python3 -B -m unittest -v'])
        request.messages.append(ModelRequest(parts=[ToolReturnPart('shell', 'failed', call.tool_call_id)]))
        self.assertFalse((await route()).tool_calls)
        handler.assert_not_awaited()

    async def test_checkpoint_recovers_only_the_current_private_queue_turn(self):
        cap = SessionWorkspace()
        cap.authorize_issue_response = AsyncMock()
        ctx = Values(prompt=None, run_id='turn:segment:2', deps=Values(
            session_id='s', actor_id='owner', turn_id='turn', root_run_id='turn:segment:2', workspace_key='w',
            history_segment=2, visibility='private', surface='web'))
        base = 'a' * 40
        cap.continued_queue_prompt = AsyncMock(return_value=f'{EDIT_PREFIX}{base}\nPrepare the existing edits for issue #1')
        cap.repair_state = AsyncMock(return_value={'status': 'ready'})
        result = await cap.wrap_model_request(ctx, request_context=Values(), handler=AsyncMock())
        cap.continued_queue_prompt.assert_awaited_once_with('s', 'owner', 'turn')
        cap.repair_state.assert_awaited_once_with('s', 'w', base)
        self.assertEqual(result.finish_reason, 'stop')

    async def test_discover_then_offer_exact_payload_once(self):
        cap = SessionWorkspace()
        cap.authorize_issue_response = AsyncMock()
        files = [{'path': 'README.md', 'content': 'exact\r\n'}]
        cap.staged_files = AsyncMock(return_value={'files': files})
        grant = Values(id='abcd1234-test', kind='github', read_only=False,
                       policy={'require_approval': True, 'allowed_tools': ['push_files']})
        ctx = Values(prompt='Publish staged files for example/project on issue-8 at ' + 'a' * 40
                     + ' through push_files with confirmation.', run_id='r',
                     deps=Values(session_id='s', workspace_key='w', turn_id='t', root_run_id='r',
                                 visibility='private', surface='web', grants=[grant]))
        params = Values(function_tools=[Values(name='github_abcd1234_push_files')])
        request = Values(messages=[], model_request_parameters=params)

        async def route():
            return await cap.wrap_model_request(ctx, request_context=request, handler=AsyncMock())

        discovery = (await route()).tool_calls[0]
        self.assertEqual(discovery.tool_name, 'search_tools')
        request.messages.append(ModelRequest(parts=[ToolReturnPart('search_tools', 'found', discovery.tool_call_id)]))
        params.function_tools = [Values(name='search_tools')]
        self.assertFalse((await route()).tool_calls)
        params.function_tools = [Values(name='github_abcd1234_push_files')]
        call = (await route()).tool_calls[0]
        self.assertEqual(call.args, {'owner': 'example', 'repo': 'project', 'branch': 'issue-8',
                                    'message': 'Apply changes for issue #8', 'files': files})
        cap.staged_files.assert_awaited_once_with('s', 'w', 'a' * 40)
        request.messages.append(ModelRequest(parts=[ToolReturnPart(call.tool_name, 'declined', call.tool_call_id,
                                                                   outcome='denied')]))
        self.assertFalse((await route()).tool_calls)
        request.messages.clear()
        grant.policy['require_approval'] = False
        self.assertFalse((await route()).tool_calls)
        cap.staged_files.assert_awaited_once()

    async def test_real_deferred_tool_reaches_confirmation_without_dispatch(self):
        cap = SessionWorkspace()
        cap.authorize_issue_response = AsyncMock()
        files = [{'path': 'README.md', 'content': 'one line\n'}]
        cap.staged_files = AsyncMock(return_value={'files': files})

        class Route(AbstractCapability):
            async def wrap_model_request(self, ctx, *, request_context, handler):
                return await cap.wrap_model_request(ctx, request_context=request_context, handler=handler)

        def fallback(*args):
            raise AssertionError('Publication unexpectedly requested inference.')

        tools = FunctionToolset()

        @tools.tool_plain
        def github_abcd1234_push_files(owner: str, repo: str, branch: str, message: str,
                                       files: list[dict[str, str]]) -> str:
            raise AssertionError('A write was dispatched before confirmation.')

        grant = Grant('abcd1234-fixture', 'github', 1, '', False,
                      {'allowed_tools': ['push_files'], 'require_approval': True})
        scope = RunScope('session', 'turn', 'actor', 'test', grants=[grant])
        runner = Agent(FunctionModel(fallback), output_type=[str, DeferredToolRequests], capabilities=[
            Route(), ConnectionTools(tools.approval_required().defer_loading(), id='moyai_connections'),
            ConnectionToolSearch(strategy=connection_search)])
        prompt = 'Publish staged files for example/project on issue-8 at ' + 'a' * 40 + ' through push_files with confirmation.'
        result = await runner.run(prompt, deps=scope, run_id=scope.root_run_id)
        self.assertIsInstance(result.output, DeferredToolRequests)
        self.assertEqual(len(result.output.approvals), 1)
        call = result.output.approvals[0]
        self.assertEqual(call.tool_name, 'github_abcd1234_push_files')
        self.assertEqual(call.args_as_dict()['files'], files)
