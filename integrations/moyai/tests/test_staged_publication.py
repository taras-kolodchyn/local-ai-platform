"""Check the arguments that enter the native confirmation boundary."""
from types import SimpleNamespace as Values
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from moyai_workspace import SessionWorkspace
from pydantic_ai.messages import ModelRequest, ToolReturnPart


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
        params = Values(function_tools=[Values(name='search_tools')])
        request = Values(messages=[], model_request_parameters=params)

        async def route():
            return await cap.wrap_model_request(ctx, request_context=request, handler=AsyncMock())

        discovery = (await route()).tool_calls[0]
        self.assertEqual(discovery.tool_name, 'search_tools')
        request.messages.append(ModelRequest(parts=[ToolReturnPart('search_tools', 'found', discovery.tool_call_id)]))
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
