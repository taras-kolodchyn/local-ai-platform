"""Verify how the local .env configures gateway and monitoring credentials."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class GatewayCredentialTests(unittest.TestCase):
    def test_explicit_credentials_and_master_key_fallbacks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'scripts').mkdir()
            for path in ('.env.example', 'compose.yaml', 'scripts/lib.sh', 'scripts/compose.sh'):
                shutil.copyfile(ROOT / path, root / path)

            for password, salt in (('fixture-password', 'fixture-salt'), ('', '')):
                with self.subTest(password=password, salt=salt):
                    (root / '.env').write_text(
                        f'LITELLM_MASTER_KEY=fixture-master\nUI_PASSWORD={password}\nLITELLM_SALT_KEY={salt}\n')
                    result = subprocess.run(
                        ['bash', 'scripts/compose.sh', 'config', '--format', 'json'],
                        cwd=root, check=True, capture_output=True, text=True, timeout=20,
                        env={**os.environ, 'COMPOSE_FILE': 'unrelated.yaml', 'COMPOSE_PATH_SEPARATOR': ';'})
                    config = json.loads(result.stdout)
                    gateway = config['services']['litellm']['environment']
                    self.assertEqual(gateway['LITELLM_MASTER_KEY'], 'fixture-master')
                    self.assertEqual(gateway['UI_PASSWORD'], password or 'fixture-master')
                    self.assertEqual(gateway['LITELLM_SALT_KEY'], salt or 'fixture-master')
                    self.assertEqual(config['secrets']['prometheus_bearer']['file'],
                                     str(root.resolve() / '.local/litellm-metrics-key'))
