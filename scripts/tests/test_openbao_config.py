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
        for path in ('.env.example', 'compose.yaml', 'compose.openbao.yaml', 'compose.openbao-local.yaml',
                     'compose.openbao-master.yaml', 'scripts/lib.sh', 'scripts/compose.sh'):
            shutil.copyfile(ROOT / path, self.root / path)

    def render(self, settings):
        (self.root / '.env').write_text(settings)
        return subprocess.run(
            ['bash', 'scripts/compose.sh', 'config', '--format', 'json'],
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

    def test_env_gateway_retains_database_salt_and_scoped_metrics_key(self):
        for configured, expected in (('fixture-database-salt', 'fixture-database-salt'), ('', 'fixture-master')):
            with self.subTest(configured=configured):
                result = self.render(f'LITELLM_MASTER_KEY=fixture-master\nLITELLM_SALT_KEY={configured}\n')
                self.assertEqual(result.returncode, 0, result.stderr)
                config = json.loads(result.stdout)
                self.assertNotIn('openbao', config['services'])
                self.assertEqual(config['services']['litellm']['environment']['LITELLM_SALT_KEY'], expected)
                self.assertTrue(config['secrets']['prometheus_bearer']['file'].endswith('/litellm-metrics-key'))

    def test_master_mode_separates_runtime_credentials_and_scrape_access(self):
        settings = ('LITELLM_UI_PASSWORD_SOURCE=openbao-local\nLITELLM_MASTER_KEY_SOURCE=openbao-local\n'
                    'LITELLM_MASTER_KEY=\nWORKSPACE_UID=501\nWORKSPACE_GID=20\n')
        result = self.render(settings)
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        agent = config['services']['litellm']
        self.assertEqual(agent['environment']['LITELLM_MASTER_KEY'], '')
        self.assertEqual(agent['environment']['LITELLM_SALT_KEY'], '')
        self.assertIn('-config=/opt/openbao/agent-master.hcl', agent['command'])
        self.assertTrue(config['secrets']['prometheus_bearer']['file'].endswith('/litellm-metrics-key'))
        self.assertNotEqual(self.render('LITELLM_MASTER_KEY_SOURCE=openbao-local\n').returncode, 0)

    def test_local_server_is_persistent_private_and_only_the_gateway_gets_the_reader(self):
        result = self.render('LITELLM_UI_PASSWORD_SOURCE=openbao-local\nWORKSPACE_UID=501\nWORKSPACE_GID=20\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        services = json.loads(result.stdout)['services']
        server, agent = services['openbao'], services['litellm']
        self.assertEqual(server['ports'][0]['host_ip'], '127.0.0.1')
        self.assertEqual(server['restart'], 'unless-stopped')
        self.assertTrue(any(v['target'] == '/var/lib/openbao' and v['type'] == 'bind' for v in server['volumes']))
        self.assertEqual(agent['depends_on']['openbao']['condition'], 'service_healthy')
        self.assertEqual(agent['environment']['BAO_ADDR'], 'https://openbao:8200')
        self.assertEqual(agent['environment']['UI_PASSWORD'], '')
        mounts = [v for v in agent['volumes'] if v['target'] == '/run/openbao-auth']
        self.assertEqual(len(mounts), 1)
        self.assertTrue(mounts[0]['read_only'])
        self.assertTrue(mounts[0]['source'].endswith('/.local/openbao-local/auth'))
        self.assertEqual({name for name, s in services.items() if 'openbao' in s['networks']}, {'openbao', 'litellm'})
