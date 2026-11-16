"""Private bootstrap recovery and actual controller reload decisions."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class MoyaiProvisioningTests(unittest.TestCase):
    def preparer(self):
        spec = importlib.util.spec_from_file_location('moyai_preparer', ROOT / 'scripts/moyai-prepare.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_replacement_keys_have_unique_aliases_for_litellm_rotation(self):
        module = self.preparer()
        self.assertTrue(hasattr(module, 'new_key_payload'), 'Unique replacement key aliases are missing')
        first, second = module.new_key_payload(), module.new_key_payload()
        self.assertNotEqual(first['key_alias'], second['key_alias'])
        for payload in (first, second):
            self.assertRegex(payload['key_alias'], r'^moyai-local-mvp-[0-9a-f]{32}$')
            self.assertEqual(payload['models'], ['local-qwen'])
            self.assertEqual(payload['object_permission'], {'mcp_servers': [], 'vector_stores': []})

    def test_missing_password_is_recovered_without_changing_existing_account(self):
        module = self.preparer()
        self.assertTrue(hasattr(module, 'bootstrap_password'), 'Bootstrap recovery is missing')
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            (local / 'runtime-config.json').write_text(json.dumps({'password': 'fixture-retained-password'}))
            self.assertEqual(module.bootstrap_password(local, retained_data=True), 'fixture-retained-password')
            self.assertEqual((local / 'admin-password').read_text().strip(), 'fixture-retained-password')
            self.assertEqual((local / 'admin-password').stat().st_mode & 0o777, 0o600)

    def test_lost_bootstrap_credentials_with_retained_data_fail_with_recovery_guidance(self):
        module = self.preparer()
        self.assertTrue(hasattr(module, 'bootstrap_password'), 'Bootstrap recovery is missing')
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            with self.assertRaisesRegex(Exception, 'restore'):
                module.bootstrap_password(local, retained_data=True)
            self.assertFalse((local / 'admin-password').exists())

    def test_changed_credentials_reload_only_existing_controller(self):
        for changed, existing, expected in [('changed', True, True), ('unchanged', True, False), ('changed', False, False)]:
            with self.subTest(changed=changed, existing=existing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'scripts').mkdir()
                for script in ('lib.sh', 'moyai.sh'):
                    shutil.copy(ROOT / 'scripts' / script, root / 'scripts' / script)
                binaries = root / 'bin'
                binaries.mkdir()
                (binaries / 'python3').write_text('#!/bin/sh\nprintf "%s\\n" "$CONFIG_CHANGE"\n')
                (binaries / 'ssh-keygen').write_text('#!/bin/sh\nexit 0\n')
                (binaries / 'docker').write_text('''#!/usr/bin/env python3_fixture
import json, os, sys
with open(os.environ['COMMAND_LOG'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[-3:] == ['ps','-q','moyai'] and os.environ['EXISTING'] == 'yes':
    print('fixture-controller')
'''.replace('/usr/bin/env python3_fixture', str(Path(os.sys.executable))))
                for binary in binaries.iterdir():
                    binary.chmod(0o755)
                log = root / 'commands.jsonl'
                result = subprocess.run(['bash', 'scripts/moyai.sh', 'up'], cwd=root,
                    env=os.environ | {'PATH': str(binaries) + os.pathsep + os.environ['PATH'],
                        'CONFIG_CHANGE': changed, 'EXISTING': 'yes' if existing else 'no',
                        'COMMAND_LOG': str(log)}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                forced = [call for call in calls if '--force-recreate' in call]
                self.assertEqual(bool(forced), expected)
                if forced:
                    self.assertEqual(forced[0][-1], 'moyai')
                    self.assertIn('--no-deps', forced[0])
