"""Check both dashboard credential sources through the lifecycle's Compose selector."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class OpenBaoConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'scripts').mkdir()
        for path in ('.env.example', 'compose.yaml', 'compose.openbao.yaml', 'scripts/lib.sh'):
            shutil.copyfile(ROOT / path, self.root / path)

    def render(self, settings):
        (self.root / '.env').write_text(settings)
        return subprocess.run(
            ['bash', '-c', 'source scripts/lib.sh && load_env && docker compose config --format json'],
            cwd=self.root, capture_output=True, text=True, timeout=20,
            env={**os.environ, 'COMPOSE_FILE': 'unrelated.yaml', 'COMPOSE_PATH_SEPARATOR': ';',
                 'LITELLM_UI_PASSWORD_SOURCE': 'openbao'},
        )

    def test_opt_in_only_changes_gateway_and_removes_compose_supplied_password(self):
        local = 'UI_PASSWORD=fixture-local-password\n'
        result = self.render(local)
        self.assertEqual(result.returncode, 0, result.stderr)
        base = json.loads(result.stdout)['services']
        default = base.pop('litellm')
        self.assertEqual(default['environment']['UI_PASSWORD'], 'fixture-local-password')
        self.assertNotIn('build', default)
        result = self.render(local + 'LITELLM_UI_PASSWORD_SOURCE=openbao\nOPENBAO_ADDR=https://bao.invalid:8200\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        enabled = json.loads(result.stdout)['services']
        agent = enabled.pop('litellm')
        self.assertEqual(enabled, base)
        self.assertEqual(agent['environment']['BAO_ADDR'], 'https://bao.invalid:8200')
        self.assertEqual(agent['environment']['UI_PASSWORD'], '')
        self.assertEqual(default['environment']['LITELLM_MASTER_KEY'], agent['environment']['LITELLM_MASTER_KEY'])
        self.assertEqual(agent['entrypoint'], ['/usr/local/bin/bao'])
        self.assertEqual(agent['ports'], default['ports'])
        auth_mount, = (mount for mount in agent['volumes'] if mount['target'] == '/run/openbao-auth')
        self.assertTrue(auth_mount['read_only'])
        self.assertIn('FROM ' + default['image'], (ROOT / 'integrations/openbao/Dockerfile').read_text().splitlines())

    def test_invalid_source_or_missing_address_cannot_fall_back_to_local_login(self):
        for settings in ('LITELLM_UI_PASSWORD_SOURCE=typo\n', 'LITELLM_UI_PASSWORD_SOURCE=openbao\n'):
            with self.subTest(settings=settings):
                self.assertNotEqual(self.render(settings).returncode, 0)
