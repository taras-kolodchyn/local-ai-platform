import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from unittest.mock import AsyncMock, patch
from gateway_skills import GatewayCatalog, pinned_skills
from observe_skill_calls import recorded_catalog
from skill_marketplace import TAG, read_marketplace
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel


class GatewaySkillTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_catalog_loads_every_gateway_skill_without_a_workspace(self):
        packaged = Path('/opt/test-skill-seeds')
        manifest = json.loads((packaged / 'catalog.json').read_text())
        plugins = []
        for entry in manifest['skills']:
            raw = (packaged / entry['name'] / 'SKILL.md').read_text()
            plugins.append({'name': entry.get('catalog_name', entry['name']), 'keywords': [TAG],
                            'description': entry['description'] + '\n\n' + raw.split('\n---\n', 1)[1].strip()})
        fetched = read_marketplace({'plugins': plugins})
        catalog = GatewayCatalog()
        reader = patch('gateway_skills.turn_skills', AsyncMock(return_value=fetched))
        reader.start()
        self.addCleanup(reader.stop)
        prepared = await catalog.for_run(SimpleNamespace(run_id='turn'))
        available = {capability.id: capability for capability in prepared.capabilities}
        self.assertEqual(len({entry['name'] for entry in manifest['skills']}), 25)
        for entry in manifest['skills']:
            name = entry['name']
            with self.subTest(skill=name):
                body = (packaged / name / 'SKILL.md').read_text().split('---\n', 2)[2].strip()
                skill = available[name]
                self.assertTrue(skill.defer_loading)
                self.assertEqual(skill.description, entry['description'])
                instructions = f'# Skill: {name}\n\n{body}'
                self.assertEqual(skill.get_instructions().strip(), instructions)

                def request_skill(messages, info):
                    returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
                    if not returns:
                        self.assertIn('load_capability', [t.name for t in info.function_tools])
                        return ModelResponse(parts=[ToolCallPart('load_capability', {'id': name})])
                    self.assertEqual(len(returns), 1)
                    self.assertEqual(returns[0].tool_name, 'load_capability')
                    self.assertEqual(returns[0].outcome, 'success')
                    self.assertEqual(returns[0].content['instructions'].strip(), instructions)
                    return ModelResponse(parts=[TextPart('loaded')])

                result = await Agent(FunctionModel(request_skill), capabilities=[catalog]).run('Load this workflow.')
                self.assertEqual(result.output, 'loaded')


    def test_a_turn_keeps_its_revision_and_a_new_turn_sees_dashboard_changes(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.db'
            first = {'test': {'description': 'Test', 'body': 'First version'}}
            self.assertEqual(pinned_skills('one', path, lambda: first), first)
            self.assertEqual(pinned_skills('two', path, lambda: {}), {})
            self.assertEqual(pinned_skills('one', path, lambda: self.fail('Reloaded a pinned turn')), first)
            first_digest, observed = recorded_catalog('one', path)
            second_digest, empty = recorded_catalog('two', path)
            self.assertEqual(observed, first)
            self.assertEqual(empty, {})
            self.assertNotEqual(first_digest, second_digest)
            with self.assertRaisesRegex(RuntimeError, 'No pinned'):
                recorded_catalog('missing', path)
