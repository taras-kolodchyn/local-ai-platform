from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from moyai.contracts import NewMessage
from pydantic_ai.exceptions import ModelRetry, ToolFailed
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.workspaces import WorkspaceReadOnlyError

import analysis_mode
from moyai_workspace import SessionWorkspace


class AnalysisModeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = SimpleNamespace(deps=SimpleNamespace(model=analysis_mode.PROFILE, session_id='s', workspace_key='k',
                                                        root_run_id='t', turn_id='t'), run_id='t',
                                   run_step=1, prompt='Analyze this example.', active_capability_ids=set())
        self.workspace = SessionWorkspace(SimpleNamespace())
        self.workspace.model_budget = AsyncMock(return_value=None)
        self.workspace.completion_message = AsyncMock(return_value=None)
        self.workspace.phase = AsyncMock(return_value=None)
        self.workspace.analysis_workspace_has_files = AsyncMock(return_value=True)

    async def test_original_web_directives_select_a_durable_model_scope(self):
        submit = AsyncMock()
        db = SimpleNamespace(submit=submit)
        analysis_mode.install_analysis_routing(db)
        for prompt, model in [('Дай відповідь у чаті, без змін у файлах.', analysis_mode.PROFILE),
                              ('Тільки аналіз: оціни функцію.', analysis_mode.PROFILE),
                              ('/analysis\nExplain this code.', analysis_mode.PROFILE),
                              ('Explain “без змін у файлах” in a comment.', 'local-qwen'),
                              ('Implement a function.', 'local-qwen')]:
            body = NewMessage(prompt=prompt, model='local-qwen', request_key='key')
            await db.submit('actor', 's', body)
            self.assertEqual(submit.await_args.args[2].model, model)
            self.assertEqual(body.model, 'local-qwen')

    async def test_revoked_lookup_cannot_repeat_a_cached_answer(self):
        self.ctx.deps.surface = 'web'
        self.ctx.deps.visibility = 'private'
        self.ctx.prompt = 'Search source for https://github.com/o/r/pull/3 policy'
        failed = ToolReturnPart('search_repository', 'Unavailable', 'search', outcome='failed')
        messages = [ModelRequest(parts=[failed], run_id=self.ctx.run_id)]
        response = await self.workspace.after_model_request(self.ctx,
            request_context=SimpleNamespace(messages=messages), response=ModelResponse(parts=[TextPart('stale citation')]))
        self.assertEqual(response.text, 'Repository search is unavailable for this work item and session.')

    async def test_workspace_refuses_all_mutations_and_commands_before_ssh(self):
        readonly = self.workspace.get_workspace(self.ctx, ref=None)
        self.assertTrue(readonly.read_only)
        for action in (readonly.write_text('x', 'demo'), readonly.make_dir('d'),
                       readonly.remove('x'), readonly.run(['echo', 'demo'])):
            with self.assertRaises(WorkspaceReadOnlyError):
                await action
        self.assertTrue(self.workspace.get_workspace(self.ctx, ref=readonly.ref).read_only)
        self.ctx.deps.model = 'local-qwen'
        self.assertFalse(self.workspace.get_workspace(self.ctx, ref=readonly.ref).read_only)

    def test_chat_response_preserves_the_answer_and_literal_xml_examples(self):
        self.ctx.prompt = 'Поясни в чаті.'
        fake = '<tool_call>\n<function=write_file>\n<parameter=path>x</parameter>\n</function>\n</tool_call>'
        ack = ModelResponse(parts=[TextPart('Ось план.\n' + fake)])
        text = analysis_mode.chat_response(self.ctx, ack).text
        self.assertIn('Ось план.', text)
        self.assertIn('не змінювалися', text)
        self.assertNotIn('<tool_call>', text)
        example = ModelResponse(parts=[TextPart('```xml\n' + fake + '\n```')])
        self.assertIs(analysis_mode.chat_response(self.ctx, example), example)
        with self.assertRaises(ModelRetry):
            analysis_mode.chat_response(self.ctx, ModelResponse(parts=[TextPart(fake)]))

    async def test_runtime_and_wire_tool_policy_includes_only_read_tools_and_reviewed_skills(self):
        other_names = ['shell', 'write_file', 'edit_file', 'save_artifact', 'write_plan', 'delegate_task',
                       'open_repository', 'open_github_issue', 'run_repository_checks', 'future_tool']
        definitions = [ToolDefinition(name=n) for n in other_names + ['list_files', 'read_file']]
        from gateway_skills import GatewaySkill
        skill = 'code-simplification'
        self.ctx.capabilities = {skill: GatewaySkill(skill, 'Simplify code', 'Review callers.')}
        loader = ToolDefinition(name='load_capability', tool_kind='capability-load',
                                parameters_json_schema={'properties': {'id': {'type': 'string'}}})
        request = ModelRequestContext(model=None, messages=[], model_settings=None,
                                      model_request_parameters=ModelRequestParameters(
                                          function_tools=[*definitions, loader], native_tools=[object()],
                                          deferred_capability_ids={skill, 'other'}))
        prepared = await self.workspace.prepare_tools(self.ctx, definitions)
        self.assertEqual({t.name for t in prepared}, {'list_files', 'read_file'})
        self.workspace.analysis_workspace_has_files.return_value = False
        self.assertEqual(await self.workspace.prepare_tools(self.ctx, definitions), [])
        params = (await self.workspace.before_model_request(self.ctx, request)).model_request_parameters
        self.assertEqual([t.name for t in params.function_tools], ['list_files', 'read_file', 'load_capability'])
        self.assertEqual(params.function_tools[-1].parameters_json_schema['properties']['id']['enum'], [skill])
        self.assertEqual(params.native_tools, [])
        self.ctx.active_capability_ids.add(skill)
        settled = await self.workspace.before_model_request(self.ctx, request)
        self.assertEqual([t.name for t in settled.model_request_parameters.function_tools], ['list_files', 'read_file'])
        for name, args in [(n, {}) for n in other_names] + [('load_capability', {'id': 'other'})]:
            with self.subTest(name=name), self.assertRaises(ToolFailed):
                await self.workspace.before_tool_execute(self.ctx, call=ToolCallPart(name, args),
                    tool_def=loader if name == loader.name else ToolDefinition(name=name), args=args)
        self.workspace.route_issue = AsyncMock(side_effect=AssertionError('Unexpected issue intake'))
        direct = AsyncMock(return_value='chat')
        self.assertEqual(await self.workspace.wrap_model_request(self.ctx, request_context=request, handler=direct), 'chat')
