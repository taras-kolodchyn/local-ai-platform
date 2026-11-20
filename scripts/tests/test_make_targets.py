"""Exercise Make's public target arguments without touching a running platform."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class MakeTargetTests(unittest.TestCase):
    def test_env_selects_repository_without_overriding_issue_or_merge_grant(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copy(ROOT / 'Makefile', root)
            scripts = root / 'scripts'
            scripts.mkdir()
            for name in ('lib.sh', 'github-command.sh'):
                shutil.copy(ROOT / 'scripts' / name, scripts)
            (root / '.env').write_text(
                "MOYAI_GITHUB_REPOSITORY=example/project\nMOYAI_GITHUB_PROJECT_PATH='a crate'\n"
                "MOYAI_AUTO_MERGE=1\nMOYAI_OPERATOR_ISSUE=99\nMOYAI_ISSUE_PROFILE=obsolete\n"
                "PUBLISHER_TEST_UNRELATED_SECRET=private\n")
            (scripts / 'github-issues.py').write_text(
                "import json, os\nprint(json.dumps([os.getenv(k) for k in "
                "('MOYAI_GITHUB_REPOSITORY', 'MOYAI_GITHUB_PROJECT_PATH', 'MOYAI_AUTO_MERGE', "
                "'MOYAI_OPERATOR_ISSUE', 'MOYAI_ISSUE_PROFILE', 'PUBLISHER_TEST_UNRELATED_SECRET')]))\n")
            result = subprocess.run(['make', 'github-app-issues', 'ISSUE=12', 'AUTO_MERGE=0',
                                     'PROFILE=rust-retry-tests'], cwd=root, capture_output=True,
                                    text=True, check=True)
            self.assertEqual(json.loads(result.stdout),
                             ['example/project', 'a crate', '0', '12', 'rust-retry-tests', None])

    def test_index_commands_pass_one_existing_directory_to_compose(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            shutil.copy(ROOT / 'Makefile', root)
            (root / 'scripts').mkdir()
            for name in ('lib.sh', 'compose.sh'):
                shutil.copy(ROOT / 'scripts' / name, root / 'scripts')
            source = root / "a developer's example"
            source.mkdir()
            docker = root / 'docker'
            docker.write_text("""#!/usr/bin/env python3
import json, os, sys
print(json.dumps([os.environ['INDEX_REPO_PATH'], sys.argv[1:]]))
""")
            docker.chmod(0o700)
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH']}
            for target in ('index', 'reindex'):
                with self.subTest(target=target):
                    ok = subprocess.run(['make', target, 'REPO=' + source.name], cwd=root, env=env,
                                        capture_output=True, text=True, check=True)
                    path, arguments = json.loads(ok.stdout)
                    self.assertEqual(path, str(source))
                    self.assertEqual(arguments[-4:], [target, '/workspace', '--repository', source.name])
                    for missing in ('', 'absent'):
                        error = subprocess.run(['make', target, 'REPO=' + missing], cwd=root, env=env,
                                               capture_output=True, text=True)
                        self.assertNotEqual(error.returncode, 0)
                        self.assertEqual(error.stdout, '')
                        self.assertFalse((root / 'absent').exists())

    def test_reset_requires_confirmation_before_it_touches_either_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copy(ROOT / 'Makefile', root)
            result = subprocess.run(['make', 'reset', 'CONFIRM_RESET=no'], cwd=root, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Refusing destructive reset', result.stderr)
            self.assertNotIn('scripts/down.sh', result.stderr)
