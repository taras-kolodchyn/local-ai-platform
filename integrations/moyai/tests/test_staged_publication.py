"""Check the arguments that enter the native confirmation boundary."""
from types import SimpleNamespace as Values
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from moyai.connection_catalog import ConnectionToolSearch, connection_search
from moyai.connections import ConnectionTools
from moyai.contracts import Grant, RunScope
from moyai_workspace import SessionWorkspace
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.toolsets import FunctionToolset


class StagedPublicationTests(IsolatedAsyncioTestCase):
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
