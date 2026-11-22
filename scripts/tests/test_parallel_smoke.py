"""Exercise the smoke script's join and exit statuses without live endpoints."""
from itertools import product
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]

# Each rendezvous requires both real shell groups to be running. The index
# marker is written only after the indexing probe succeeds.
PROBES = """
curl() {
  case "${!#}" in
    http://127.0.0.1:8000/search)
      [[ -f indexed ]] || return 91
      ;;
    http://127.0.0.1:4000/local_retrieval/mcp)
      if [[ ! -f mcp-started ]]; then
        [[ -f indexed ]] || return 91
        rendezvous mcp vector
        [[ $FAIL_GROUP != mcp ]] || return 40
        if [[ $FAIL_GROUP == vector ]]; then sleep 0.25; fi
      fi
      ;;
  esac
}
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
      if [[ $FAIL_GROUP == index ]]; then sleep 0.25; fi
      [[ $FAIL_GROUP != qwen ]] || return 38
      ;;
    'scripts/retrieval_probe.py index')
      rendezvous retrieval qwen
      [[ $FAIL_GROUP != index ]] || return 37
      touch indexed
      ;;
    'scripts/vector-smoke.py --indexed')
      [[ -f indexed ]] || return 91
      rendezvous vector mcp
      [[ $FAIL_GROUP != vector ]] || return 39
      if [[ $FAIL_GROUP == mcp ]]; then sleep 0.25; fi
      echo VECTOR_DONE
      ;;
  esac
}
"""


class ParallelSmokeTests(unittest.TestCase):
    def test_join_before_monitoring_and_log_scan(self):
        cases = [('neither', 0), ('index', 37), ('qwen', 38), ('vector', 39), ('mcp', 40)]
        for timings, (group, status) in product(('0', '1'), cases):
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
                mcp = 'Read-only PostgreSQL MCP tool call returned bounded metadata'
                self.assertEqual(qwen in text, group != 'qwen', text)
                self.assertEqual('VECTOR_DONE' in text, group not in ('index', 'vector'), text)
                self.assertEqual(mcp in text, group not in ('index', 'mcp'), text)
                for name in ('vector', 'mcp'):
                    self.assertEqual((root / (name + '-started')).exists(), group != 'index', text)
                for marker in ['MONITORING', 'LOG_SCAN', 'End-to-end smoke tests passed']:
                    self.assertEqual(marker in text, status == 0, text)
                for lane in ('Qwen', 'retrieval/MCP', 'Indexing', 'Vector search', 'MCP'):
                    ran = group != 'index' or lane not in ('Vector search', 'MCP')
                    marker = f'[timings] {lane} '
                    self.assertEqual(marker in text, timings == '1' and ran, text)
                    if status == 0 and timings == '1':
                        self.assertLess(text.index(marker), text.index('MONITORING'))
                if status == 0:
                    for success in (qwen, 'VECTOR_DONE', mcp):
                        self.assertLess(text.index(success), text.index('MONITORING'))
                    self.assertGreater(text.index('LOG_SCAN'), text.index('MONITORING'))
