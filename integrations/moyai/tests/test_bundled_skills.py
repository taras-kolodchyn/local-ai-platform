import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from moyai.agent import PACKAGE_ROOT, bundled_skill_catalog
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel


class BundledSkillTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_catalog_offers_the_packaged_instructions_without_a_workspace(self):
        packaged = Path(__file__).resolve().parents[1] / 'skills'
        manifest = json.loads((packaged / 'catalog.json').read_text())
        catalog = bundled_skill_catalog(PACKAGE_ROOT / 'skills', '/opt/moyai/skills')
        prepared = await catalog.for_run(SimpleNamespace(workspace=None))
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
