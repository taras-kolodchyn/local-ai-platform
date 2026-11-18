from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from moyai.agent import PACKAGE_ROOT, bundled_skill_catalog
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import ToolDefinition

from engineering_skills import SELECT_SKILLS
from moyai_workspace import SessionWorkspace


SKILL = 'planning-and-task-breakdown'
MARKUP = '<function=load_capability>\n<parameter=id>\n' + SKILL + '\n</parameter>\n</function>\n</tool_call>'


class SkillCallTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.workspace = SessionWorkspace(SimpleNamespace())
        self.workspace.model_budget = AsyncMock(return_value=None)
        self.workspace.phase = AsyncMock(return_value=None)
        self.workspace.completion_message = AsyncMock(return_value=None)
        self.ctx = SimpleNamespace(run_id='root', run_step=1, active_capability_ids=set(),
                                   deps=SimpleNamespace(root_run_id='root', turn_id='turn',
                                                        session_id='s', workspace_key='w'))
        self.loader = ToolDefinition(name='load_capability', tool_kind='capability-load',
                                    parameters_json_schema={'type': 'object', 'properties': {'id': {'type': 'string'}},
                                                            'required': ['id'], 'additionalProperties': False})
        self.request = ModelRequestContext(model=None, model_settings={'temperature': 0}, messages=[],
            model_request_parameters=ModelRequestParameters(function_tools=[self.loader], deferred_capability_ids={SKILL}))

    async def check_response(self, *parts):
        response = ModelResponse(parts=list(parts))
        return await self.workspace.after_model_request(self.ctx, request_context=self.request, response=response)

    async def test_simulated_call_retries_with_the_native_loader_and_then_releases_tool_choice(self):
        with self.assertRaises(ModelRetry) as error:
            await self.check_response(TextPart("I'll load the requested skill.\n\n" + MARKUP))
        self.request.messages = [ModelRequest(parts=[RetryPromptPart(content=error.exception.message)])]
        forced = await self.workspace.before_model_request(self.ctx, self.request)
        self.assertEqual(forced.model_settings['tool_choice'], ['load_capability'])
        schema = forced.model_request_parameters.function_tools[0].parameters_json_schema
        self.assertEqual(schema['properties']['id']['enum'], [SKILL])
        self.assertNotIn('enum', self.loader.parameters_json_schema['properties']['id'])
        self.request.messages = [ModelRequest(parts=[UserPromptPart(content=error.exception.message)])]
        normal = await self.workspace.before_model_request(self.ctx, self.request)
        self.assertNotIn('tool_choice', normal.model_settings)

    async def test_examples_restricted_tools_and_real_calls_stay_untouched(self):
        for text in ('```xml\n' + MARKUP + '\n```', 'Example: `<function=load_capability>`',
                     MARKUP.replace(SKILL, 'unknown-skill'), MARKUP.replace('load_capability', 'shell')):
            with self.subTest(text=text):
                self.assertEqual((await self.check_response(TextPart(text))).text, text)
        call = ToolCallPart('load_capability', {'id': SKILL})
        self.assertEqual((await self.check_response(call)).tool_calls, [call])
        self.request.model_request_parameters.function_tools = []
        self.assertEqual((await self.check_response(TextPart(MARKUP))).text, MARKUP)

    async def test_batch_uses_only_offered_inactive_skills_from_this_turn(self):
        self.request.messages = [ModelRequest(parts=[ToolReturnPart(SELECT_SKILLS, {'skills': [SKILL]})], run_id='root')]
        forced = await self.workspace.before_model_request(self.ctx, self.request)
        self.assertEqual(forced.model_settings['tool_choice'], ['load_capability'])
        self.request.model_settings['tool_choice'] = 'none'
        self.assertIs(await self.workspace.before_model_request(self.ctx, self.request), self.request)
        del self.request.model_settings['tool_choice']
        self.ctx.active_capability_ids.add(SKILL)
        self.assertIs(await self.workspace.before_model_request(self.ctx, self.request), self.request)
        self.ctx.active_capability_ids.clear()
        self.request.messages[0].run_id = 'earlier-turn'
        self.assertIs(await self.workspace.before_model_request(self.ctx, self.request), self.request)

    async def test_selection_rejects_skills_outside_the_offered_set(self):
        self.ctx.deps.surface = 'web'
        self.ctx.prompt = 'Plan a change.'
        selector = ToolDefinition(name=SELECT_SKILLS)
        self.request.model_request_parameters.function_tools = [selector, self.loader]
        forced = await self.workspace.before_model_request(self.ctx, self.request)
        self.assertEqual(forced.model_settings['tool_choice'], [SELECT_SKILLS])
        for names, active in ((['documentation-and-adrs'], set()), ([SKILL], {SKILL}), ([1], set())):
            self.ctx.active_capability_ids = active
            response = ModelResponse(parts=[ToolCallPart(SELECT_SKILLS, {'skills': names})])
            with self.assertRaises(ModelRetry):
                await self.workspace.after_model_request(self.ctx, request_context=forced, response=response)

    async def test_explicit_repository_intake_and_attached_phases_keep_their_native_tools(self):
        self.ctx.deps.surface = 'web'
        self.ctx.prompt = 'Complete the registered task for repository_id repo_example.'
        self.assertFalse(self.workspace.engineering_skills.needs_selection(self.ctx, []))
        self.workspace.phase.return_value = 'analyzing'
        self.request.messages = [ModelRequest(parts=[ToolReturnPart(SELECT_SKILLS, {'skills': [SKILL]})], run_id='root')]
        self.assertIs(await self.workspace.before_model_request(self.ctx, self.request), self.request)
        self.assertEqual((await self.check_response(TextPart(MARKUP))).text, MARKUP)

    async def test_textual_repository_listing_is_retried_as_a_native_read_only_call(self):
        self.ctx.deps.surface = 'web'
        self.ctx.prompt = ('Complete the registered repository task for repository_id repo_demo.\n'
                           'Use list_repositories, then open_repository with this ID.')
        params = self.request.model_request_parameters
        listing = ToolDefinition(name='list_repositories')
        params.function_tools = [listing, self.loader]
        markup = TextPart("I'll list the repositories.\n\n<function=list_repositories>\n</function>\n</tool_call>")
        with self.assertRaises(ModelRetry) as error:
            await self.check_response(markup)
        self.request.messages = [ModelRequest(parts=[RetryPromptPart(content=error.exception.message)])]
        forced = await self.workspace.before_model_request(self.ctx, self.request)
        self.assertEqual(forced.model_settings.get('tool_choice'), ['list_repositories'])
        self.assertEqual(forced.model_request_parameters.function_tools, [listing])
        params.function_tools = [self.loader]
        self.assertIs(await self.workspace.before_model_request(self.ctx, self.request), self.request)
        self.assertEqual((await self.check_response(markup)).text, markup.content)

    async def test_repair_is_request_local_and_requires_the_exact_native_result(self):
        with self.assertRaises(ModelRetry) as error:
            await self.check_response(TextPart(MARKUP))
        history = [ModelRequest(parts=[UserPromptPart('Original user task')]),
                   ModelResponse(parts=[TextPart(MARKUP)]),
                   ModelRequest(parts=[RetryPromptPart(content=error.exception.message)])]
        self.request.messages = history
        forced = await self.workspace.before_model_request(self.ctx, self.request)
        self.assertEqual(len(history), 3)
        self.assertEqual(len(forced.messages), 1)
        self.assertNotIn(MARKUP, str(forced.messages))
        for invalid in (TextPart('Loaded.'), ToolCallPart('load_capability', {'id': 'something-else'})):
            with self.assertRaises(ModelRetry):
                await self.workspace.after_model_request(self.ctx, request_context=forced,
                                                        response=ModelResponse(parts=[invalid]))
        real = ModelResponse(parts=[ToolCallPart('load_capability', {'id': SKILL})])
        self.assertIs(await self.workspace.after_model_request(self.ctx, request_context=forced, response=real), real)

    async def test_textual_open_requires_a_current_listing_and_the_requested_id(self):
        self.ctx.deps.surface = 'web'
        self.ctx.prompt = 'Use list_repositories, then open_repository for repository_id repo_demo.'
        params = self.request.model_request_parameters
        opening = ToolDefinition(name='open_repository', parameters_json_schema={
            'type': 'object', 'properties': {'repository_id': {'type': 'string'}}})
        params.function_tools = [ToolDefinition(name='list_repositories'), opening]
        markup = TextPart('<function=open_repository>\n<parameter=repository_id>\nrepo_demo\n</parameter>\n</function>\n</tool_call>')
        listing = ToolReturnPart('list_repositories', [{'repository_id': 'repo_demo'}])
        self.request.messages = [ModelRequest(parts=[listing], run_id='old-turn')]
        self.assertEqual((await self.check_response(markup)).text, markup.content)
        self.request.messages[0].run_id = self.ctx.run_id
        other = TextPart(markup.content.replace('repo_demo', 'repo_other'))
        self.assertEqual((await self.check_response(other)).text, other.content)
        with self.assertRaises(ModelRetry) as retry:
            await self.check_response(markup)
        self.request.messages.append(ModelRequest(parts=[RetryPromptPart(content=retry.exception.message)]))
        forced = await self.workspace.before_model_request(self.ctx, self.request)
        self.assertEqual(forced.model_settings['tool_choice'], ['open_repository'])
        with self.assertRaises(ModelRetry):
            await self.workspace.after_model_request(self.ctx, request_context=forced,
                response=ModelResponse(parts=[ToolCallPart('open_repository', {'repository_id': 'repo_other'})]))
        real = ModelResponse(parts=[ToolCallPart('open_repository', {'repository_id': 'repo_demo'})])
        self.assertIs(await self.workspace.after_model_request(self.ctx, request_context=forced, response=real), real)

    async def test_newly_attached_task_only_offers_native_tools_for_its_current_phase(self):
        self.ctx.deps.surface = 'web'
        self.ctx.prompt = 'Repair this repository.'
        self.workspace.phase.return_value = 'analyzing'
        read = ToolDefinition(name='read_file')
        self.request.model_request_parameters.function_tools = [read, self.loader, ToolDefinition(name='write_file')]
        self.request.messages = [ModelRequest(parts=[ToolReturnPart('open_repository', {})], run_id='root')]
        scoped = await self.workspace.before_model_request(self.ctx, self.request)
        self.assertEqual(scoped.model_settings['tool_choice'], 'required')
        self.assertEqual(scoped.model_request_parameters.function_tools, [read])
        self.assertFalse(scoped.model_request_parameters.deferred_capability_ids)
        self.request.messages[0].run_id = 'earlier-turn'
        self.assertIs(await self.workspace.before_model_request(self.ctx, self.request), self.request)

    async def test_sdk_executes_the_loader_and_restores_the_original_user_task(self):
        calls = []
        task = 'Make a plan for the example.'
        self.workspace.backend.ledger = SimpleNamespace(status=lambda *scope: None)
        catalog = bundled_skill_catalog(PACKAGE_ROOT / 'skills', '/opt/moyai/skills')

        def respond(messages, info):
            calls.append((info.model_settings or {}).get('tool_choice'))
            if len(calls) == 1:
                return ModelResponse(parts=[TextPart(MARKUP)])
            if len(calls) == 2:
                self.assertEqual([tool.name for tool in info.function_tools], ['load_capability'])
                self.assertNotIn(task, str(messages))
                return ModelResponse(parts=[ToolCallPart('load_capability', {'id': SKILL})])
            parts = [part for m in messages for part in m.parts]
            self.assertTrue(any(isinstance(p, UserPromptPart) and p.content == task for p in parts))
            self.assertTrue(any(isinstance(p, ToolReturnPart) and p.tool_name == 'load_capability'
                                and '# Skill: ' + SKILL in p.content['instructions'] for p in parts))
            return ModelResponse(parts=[TextPart('Finished')])

        agent = Agent(FunctionModel(respond), capabilities=[self.workspace, catalog])
        result = await agent.run(task, deps=self.ctx.deps, run_id='root')
        self.assertEqual(result.output, 'Finished')
        self.assertEqual(calls, [None, ['load_capability'], None])

    async def test_sdk_selection_loads_each_selected_skill_once_and_allows_an_empty_selection(self):
        self.workspace.backend.ledger = SimpleNamespace(status=lambda *scope: None)
        self.ctx.deps.surface = 'web'
        catalog = bundled_skill_catalog(PACKAGE_ROOT / 'skills', '/opt/moyai/skills')
        for wanted in ([], [SKILL, 'documentation-and-adrs']):
            choices = []

            def respond(messages, info):
                choice = (info.model_settings or {}).get('tool_choice')
                choices.append(choice)
                if choice == [SELECT_SKILLS]:
                    self.assertIn('Example task', str(messages))
                    return ModelResponse(parts=[ToolCallPart(SELECT_SKILLS, {'skills': wanted})])
                if choice == ['load_capability']:
                    skill_id, = info.function_tools[0].parameters_json_schema['properties']['id']['enum']
                    return ModelResponse(parts=[ToolCallPart('load_capability', {'id': skill_id})])
                return ModelResponse(parts=[TextPart('Finished')])

            agent = Agent(FunctionModel(respond), capabilities=[self.workspace, catalog])
            self.assertEqual((await agent.run('Example task', deps=self.ctx.deps, run_id='root')).output, 'Finished')
            self.assertEqual(choices, [[SELECT_SKILLS]] + [['load_capability']] * len(wanted) + [None])
