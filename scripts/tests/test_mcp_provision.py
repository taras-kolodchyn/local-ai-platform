"""Verify startup decisions against a disposable PostgreSQL cluster and real Compose."""
import os
from pathlib import Path
import secrets
import subprocess
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / 'fixtures' / 'mcp-compose.yaml'


class McpProvisionTests(unittest.TestCase):
    def setUp(self):
        self.project = 'mcp-role-test-' + uuid.uuid4().hex[:12]
        self.env = {**os.environ, 'COMPOSE_FILE': str(FIXTURE), 'COMPOSE_PROJECT_NAME': self.project,
                    'COMPOSE_ENV_FILES': str(ROOT / '.env.example'),
                    'MCP_TEST_PROJECT': self.project, 'MCP_TEST_FILE': str(FIXTURE),
                    'POSTGRES_PASSWORD': secrets.token_hex(16),
                    'MCP_POSTGRES_PASSWORD': secrets.token_hex(16), 'LOG_LEVEL': 'INFO'}
        self.env.pop('COMPOSE_PROFILES', None)
        self.addCleanup(self.compose, 'down', '--volumes', '--remove-orphans')

    def command(self, args, *, stdin=None, check=True, env=None):
        result = subprocess.run(args, cwd=ROOT, env=self.env | (env or {}), input=stdin,
                                text=True, capture_output=True, timeout=120)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def compose(self, *args, **kwargs):
        return self.command(['docker', 'compose', *args], **kwargs)

    def start(self):
        return self.command(['bash', '-c', """
            source scripts/lib.sh
            source scripts/mcp-provision.sh
            export COMPOSE_PROJECT_NAME="$MCP_TEST_PROJECT" COMPOSE_FILE="$MCP_TEST_FILE"
            start_core
        """]).stdout

    def provisioner_start(self):
        cid = self.compose('ps', '-aq', 'mcp-provisioner').stdout.strip()
        return self.command(['docker', 'inspect', '-f', '{{.State.StartedAt}}', cid]).stdout.strip()

    def admin(self, sql, database='local_ai'):
        return self.compose('exec', '-T', 'postgres', 'psql', '-XqAt', '-U', 'localai',
                            '-d', database, '-v', 'ON_ERROR_STOP=1', stdin=sql).stdout.strip()

    def reader(self, password, sql):
        return self.compose('exec', '-T', '-e', 'PGPASSWORD', 'postgres', 'psql', '-XwqAt',
                            '-v', 'ON_ERROR_STOP=1', '-d',
                            'host=postgres dbname=local_ai user=mcp_reader require_auth=scram-sha-256',
                            env={'PGPASSWORD': password}, stdin=sql, check=False)

    def test_cold_repeat_rotation_and_database_restore(self):
        self.assertIn('Provisioning MCP role', self.start())
        self.admin('CREATE TABLE public.saved (value integer); INSERT INTO saved VALUES (42);')
        original_start = self.provisioner_start()
        self.assertIn('skipping mcp-provisioner', self.start())
        self.assertEqual(self.provisioner_start(), original_start)

        old = self.env['MCP_POSTGRES_PASSWORD']
        new = self.env['MCP_POSTGRES_PASSWORD'] = secrets.token_hex(16)
        self.assertIn('Provisioning MCP role', self.start())
        rotated_start = self.provisioner_start()
        self.assertNotEqual(rotated_start, original_start)
        self.assertIn('password authentication failed', self.reader(old, 'SELECT 1').stderr)
        self.assertEqual(self.reader(new, 'SELECT value FROM saved').stdout.strip(), '42')

        # A logical restore can preserve the cluster login and data but omit ACLs.
        dump = self.compose('exec', '-T', 'postgres', 'pg_dump', '-U', 'localai',
                            '--no-acl', 'local_ai').stdout
        self.admin('DROP DATABASE local_ai WITH (FORCE); CREATE DATABASE local_ai;', 'postgres')
        self.admin(dump)
        self.assertIn('permission denied', self.reader(new, 'SELECT value FROM saved').stderr)
        self.assertIn('Provisioning MCP role', self.start())
        restored_start = self.provisioner_start()
        self.assertNotEqual(restored_start, rotated_start)
        self.assertEqual(self.reader(new, 'SELECT value FROM saved').stdout.strip(), '42')
        self.admin('CREATE TABLE public.after_restore (value integer);')
        access = self.reader(new, 'SELECT count(*) FROM after_restore; '
                                  'SHOW default_transaction_read_only; SHOW statement_timeout;')
        self.assertEqual(access.stdout.splitlines(), ['0', 'on', '5s'])
        self.assertIn('skipping mcp-provisioner', self.start())
        self.assertEqual(self.provisioner_start(), restored_start)

        # Default privileges and direct grants must still count when existing
        # tables are readable or PUBLIC happens to provide effective access.
        for drift in (
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM mcp_reader',
            'REVOKE CONNECT ON DATABASE local_ai FROM mcp_reader',
            'REVOKE USAGE ON SCHEMA public FROM mcp_reader',
            'REVOKE SELECT ON saved FROM mcp_reader',
            "ALTER ROLE mcp_reader IN DATABASE local_ai SET statement_timeout = '30s'",
            'ALTER ROLE mcp_reader NOLOGIN',
            'GRANT UPDATE ON saved TO mcp_reader',
            'ALTER ROLE mcp_reader CREATEROLE',
        ):
            with self.subTest(drift=drift):
                self.admin(drift)
                self.assertIn('Provisioning MCP role', self.start())
