"""Exercise the smoke script's join and exit statuses without live endpoints."""
from itertools import product
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]

# Stub the external probes. Rendezvous inside the two real shell groups so a
# sequential implementation times out. Keep Qwen active during retrieval failure.
PROBES = """
curl() { :; }
jq() { echo multiply; }
lsof() { :; }
grep() { :; }
rg() { echo LOG_SCAN; return 1; }
bash() {
  if [[ $1 == scripts/check-observability.sh ]]; then echo MONITORING; fi
}
rendezvous() {
  touch "$1-started"
  for _ in {1..150}; do
    [[ -e $2-started ]] && return 0
    sleep 0.02
  done
  return 90
}
python3() {
  case "$*" in
    'scripts/compression-smoke.py')
      rendezvous qwen retrieval
      sleep 0.25
      [[ $FAIL_GROUP != qwen ]] || return 38
      ;;
    'scripts/retrieval_probe.py index')
      rendezvous retrieval qwen
      [[ $FAIL_GROUP != retrieval ]] || return 37
      ;;
  esac
}
"""


class ParallelSmokeTests(unittest.TestCase):
    def test_join_before_monitoring_and_log_scan(self):
        for timings, (group, status) in product(('0', '1'), [('neither', 0), ('qwen', 38), ('retrieval', 37)]):
            with self.subTest(timings=timings, group=group), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / 'scripts').mkdir()
                for script in ['smoke-test.sh', 'lib.sh']:
                    shutil.copy(ROOT / 'scripts' / script, root / 'scripts')
                (root / '.env').touch()
                (root / '.local').mkdir()
                (root / '.local/litellm-api-key').write_text('fixture')
                probes = root / 'probes.sh'
                probes.write_text(PROBES)
                log = root / 'output'
                # A regular file lets us observe the parent's exit immediately,
                # even if a regression leaves a child holding stdout open.
                with log.open('w') as output:
                    result = subprocess.run(
                        ['bash', 'scripts/smoke-test.sh'], cwd=root, timeout=10,
                        env=os.environ | {'BASH_ENV': str(probes), 'FAIL_GROUP': group, 'TIMINGS': timings},
                        stdout=output, stderr=subprocess.STDOUT)
                text = log.read_text()
                self.assertEqual(result.returncode, status, text)
                qwen = 'Explicit Redis cache miss/hit path verified'
                retrieval = 'Read-only PostgreSQL MCP tool call returned bounded metadata'
                self.assertEqual(qwen in text, group != 'qwen', text)
                self.assertEqual(retrieval in text, group != 'retrieval', text)
                for marker in ['MONITORING', 'LOG_SCAN', 'End-to-end smoke tests passed']:
                    self.assertEqual(marker in text, status == 0, text)
                for lane in ('Qwen', 'retrieval/MCP'):
                    self.assertEqual(f'[timings] {lane} ' in text, timings == '1', text)
                if status == 0 and timings == '1':
                    for lane in ('Qwen', 'retrieval/MCP'):
                        self.assertLess(text.index(f'[timings] {lane} '), text.index('MONITORING'))
                if status == 0:
                    self.assertGreater(text.index('MONITORING'), max(text.index(qwen), text.index(retrieval)))
                    self.assertGreater(text.index('LOG_SCAN'), text.index('MONITORING'))
