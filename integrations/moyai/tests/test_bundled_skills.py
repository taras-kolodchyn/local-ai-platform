import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from moyai.agent import PACKAGE_ROOT, bundled_skill_catalog


class BundledSkillTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_catalog_offers_the_packaged_instructions_without_a_workspace(self):
        packaged = Path(__file__).resolve().parents[1] / 'skills'
        manifest = json.loads((packaged / 'catalog.json').read_text())
        catalog = bundled_skill_catalog(PACKAGE_ROOT / 'skills', '/opt/moyai/skills')
        prepared = await catalog.for_run(SimpleNamespace(workspace=None))
        available = {capability.id: capability for capability in prepared.capabilities}
        for entry in manifest['skills']:
            name = entry['name']
            with self.subTest(skill=name):
                body = (packaged / name / 'SKILL.md').read_text().split('---\n', 2)[2].strip()
                skill = available[name]
                self.assertTrue(skill.defer_loading)
                self.assertEqual(skill.description, entry['description'])
                self.assertEqual(skill.get_instructions().strip(), f'# Skill: {name}\n\n{body}')
