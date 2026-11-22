"""Run the actual Temporal service and volume initializer in a disposable project."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / 'fixtures' / 'temporal-compose.yaml'


class TemporalVolumeTests(unittest.TestCase):
    def setUp(self):
        project = 'temporal-volume-test-' + uuid.uuid4().hex[:12]
        self.volume = project + '-data'
        self.env = dict(os.environ, TEST_TEMPORAL_VOLUME=self.volume)
        self.flags = ['--project-name', project, '--file', str(FIXTURE)]
        # Compose removes the current volume. A successful switch also leaves the old one.
        self.addCleanup(self.command, 'docker', 'volume', 'rm', self.volume, check=False)
        self.addCleanup(self.compose, 'down', '--volumes')

    def command(self, *args, check=True):
        result = subprocess.run(args, cwd=ROOT, env=self.env, text=True, capture_output=True, timeout=150)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def compose(self, *args):
        return self.command('docker', 'compose', *self.flags, *args).stdout.strip()

    def start(self, *flags, check=True):
        return self.command('bash', '-c', """
            source scripts/lib.sh
            source scripts/moyai-volume.sh
            start_moyai_services "$@"
        """, 'test', *self.flags, *flags, check=check)

    def init_start(self):
        cid = self.compose('ps', '--all', '--quiet', 'moyai-volume-init')
        return self.command('docker', 'inspect', '--format', '{{.State.StartedAt}}', cid).stdout.strip()

    def owner(self):
        return self.compose('exec', '-T', 'moyai-temporal', 'stat', '-c', '%u:%g', '/data')

    def test_new_current_wrong_owner_and_replaced_volume(self):
        self.assertIn('Initializing Temporal', self.start().stdout)
        self.assertEqual(self.owner(), '1000:1000')
        cold = self.init_start()

        self.assertIn('skipping moyai-volume-init', self.start().stdout)
        self.assertEqual(self.init_start(), cold)

        repaired = cold
        for wrong_owner in ('0:1000', '1000:0'):
            with self.subTest(owner=wrong_owner):
                self.compose('exec', '-T', '--user', '0', 'moyai-temporal', 'chown', wrong_owner, '/data')
                self.assertEqual(self.owner(), wrong_owner)
                self.assertIn('Initializing Temporal', self.start().stdout)
                self.assertEqual(self.owner(), '1000:1000')
                self.assertNotEqual(self.init_start(), repaired)
                repaired = self.init_start()

        # Keep the healthy old container up while Compose is pointed at a new volume.
        # Both volumes belong exclusively to this random test project.
        self.env['TEST_TEMPORAL_VOLUME'] = self.volume + '-replacement'
        self.assertIn('Initializing Temporal', self.start().stdout)
        self.assertEqual(self.owner(), '1000:1000')
        replacement = self.init_start()
        self.assertNotEqual(replacement, repaired)
        self.assertIn('skipping moyai-volume-init', self.start().stdout)
        self.assertEqual(self.init_start(), replacement)

    def test_new_volume_with_failed_init_blocks_temporal(self):
        with tempfile.TemporaryDirectory() as directory:
            overlay = Path(directory) / 'readonly.yaml'
            overlay.write_text('services:\n  moyai-volume-init:\n    volumes: [moyai_temporal:/data:ro]\n')
            result = self.start('--file', str(overlay), check=False)
        self.assertIn('Initializing Temporal', result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.compose('ps', '--quiet', 'moyai-temporal'), '')
