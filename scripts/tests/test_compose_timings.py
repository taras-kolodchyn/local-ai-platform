"""Exercise Compose progress, including a slow service and a failed wait."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >&2
printf '%s\\n' \
  '{"id":"Container db","status":"Working","text":"Waiting"}' \
  '{"id":"Container setup","status":"Working","text":"Starting"}' \
  '{"id":"Container db","status":"Done","text":"Healthy"}' \
  '{"id":"Container setup","status":"Done","text":"Started"}' \
  '{"id":"Container setup","status":"Working","text":"Waiting"}' >&2
if [ "$FAIL_WAIT" = 1 ]; then
  echo 'dependency failed to start' >&2
  exit 23
fi
sleep 4
echo '{"id":"Container setup","status":"Done","text":"Exited"}' >&2
"""


class ComposeTimingTests(unittest.TestCase):
    def run_compose(self, fail=False):
        with tempfile.TemporaryDirectory() as temp:
            docker = Path(temp, 'docker')
            docker.write_text(DOCKER)
            docker.chmod(0o755)
            return subprocess.run(
                ['bash', '-c', 'source scripts/lib.sh; compose_wait Core up --detach --wait'],
                cwd=ROOT, env=os.environ | {'PATH': temp + ':' + os.environ['PATH'],
                                            'FAIL_WAIT': str(int(fail)), 'TIMINGS': '1'},
                capture_output=True, text=True, timeout=12)

    def test_wait_for_one_shot_after_database_is_healthy(self):
        result = self.run_compose()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('compose --progress json up --detach --wait', result.stderr)
        self.assertRegex(result.stderr, r'db Healthy \(Waiting [\d.]+s\)')
        self.assertRegex(result.stderr, r'setup Started \(Starting [\d.]+s\)')
        pending = [line for line in result.stderr.splitlines() if 'pending:' in line]
        self.assertTrue(pending)
        self.assertTrue(all('setup Waiting' in line and 'db' not in line for line in pending))
        self.assertLess(result.stderr.index(pending[0]), result.stderr.index('setup Exited (Waiting'))

    def test_compose_error_reaches_caller(self):
        result = self.run_compose(fail=True)
        self.assertEqual(result.returncode, 23)
        self.assertIn('dependency failed to start', result.stderr)
        self.assertIn('last reported pending: setup Waiting', result.stderr)
