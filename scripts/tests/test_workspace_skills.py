import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('installer', Path(__file__).parents[1] / 'install-workspace-skills.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SkillsTests(unittest.TestCase):
    def test_local_edit_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bundle = root / 'bundle'; bundle.mkdir()
            (bundle / 'SKILL.md').write_text('first version')
            home = root / 'home'
            module.install_bundle(bundle, home)
            installed = home / 'skills/bundle/SKILL.md'
            installed.write_text('local revision')
            result = module.install_bundle(bundle, home)
            self.assertEqual(result['status'], 'local_changes')
            self.assertEqual(installed.read_text(), 'local revision')

    def test_symlink_source_rejected_without_installing(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); bundle=root/'bundle'; bundle.mkdir()
            (bundle/'SKILL.md').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError): module.install_bundle(bundle, root/'home')
            self.assertFalse((root/'home/skills/bundle/SKILL.md').exists())
