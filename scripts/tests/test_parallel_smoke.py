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
    http://127.0.0.1:8787/health)
      if [[ ${STARTUP:-0} == 1 ]]; then [[ -f indexed ]] || return 91; fi
      [[ $FAIL_GROUP != moyai_ui ]] || return 43
      ;;
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
rg() { echo LOG_SCAN; [[ $FAIL_GROUP != log_error ]] || return 2; [[ $FAIL_GROUP == logs ]]; }
bash() {
  if [[ $1 == scripts/check-observability.sh ]]; then echo MONITORING; [[ $FAIL_GROUP != monitoring ]] || return 42; fi
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
    'scripts/check-model-bindings.py')
      [[ $FAIL_GROUP != gateway ]] || return 41
      ;;
    'scripts/compression-smoke.py')
      rendezvous qwen retrieval
      if [[ $FAIL_GROUP == index ]]; then sleep 0.25; fi
      [[ $FAIL_GROUP != qwen ]] || return 38
      ;;
    'scripts/retrieval_probe.py index')
      echo INDEX_RAN >&2
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
    def test_startup_pipe_overlaps_qwen_and_gates_moyai_search_and_monitoring(self):
        counts = '{"files_indexed": 2}'
        # The producer cannot finish until Qwen is running. EOF on failure must
        # never release the UI or search probes against a stale index report.
        command = """
        prepare() {
          [[ $FAIL_GROUP != gateway ]] || return 0
          rendezvous retrieval qwen || return 90
          [[ $FAIL_GROUP != startup ]] || return 0
          touch indexed
          printf '%s\\n' "$COUNTS"
        }
        prepare | /bin/bash scripts/smoke-test.sh --await-startup
        """
        cases = {'success': 0, 'gateway': 41, 'qwen': 38, 'startup': 1,
                 'vector': 39, 'mcp': 40, 'moyai_ui': 43, 'monitoring': 42, 'logs': 1, 'log_error': 1}
        for timing, (group, status) in product(('0', '1'), cases.items()):
            with self.subTest(timing=timing, group=group), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / 'scripts').mkdir()
                for script in ('smoke-test.sh', 'lib.sh'):
                    shutil.copy(ROOT / 'scripts' / script, root / 'scripts')
                (root / '.env').touch()
                smoke = root / '.local/smoke'
                smoke.mkdir(parents=True)
                (root / '.local/litellm-api-key').write_text('fixture')
                report = smoke / 'ingestion.json'
                report.write_text('stale')
                probes = root / 'probes.sh'
                probes.write_text(PROBES)
                log = root / 'output'
                with log.open('w') as output:
                    result = subprocess.run(['/bin/bash', '-c', command], cwd=root, timeout=10,
                                            env=os.environ | {'BASH_ENV': str(probes), 'STARTUP': '1',
                                                              'FAIL_GROUP': group, 'TIMINGS': timing,
                                                              'COUNTS': counts},
                                            stdout=output, stderr=subprocess.STDOUT)
                text = log.read_text()
                self.assertEqual(result.returncode, status, text)
                searched = group not in ('gateway', 'startup', 'moyai_ui')
                self.assertEqual(report.read_text().strip(), counts if searched else 'stale', text)
                self.assertEqual('Local Moyai is healthy' in text, searched)
                self.assertEqual((root / 'vector-started').exists(), searched)
                self.assertEqual((root / 'mcp-started').exists(), searched)
                self.assertNotIn('INDEX_RAN', text)
                self.assertEqual('MONITORING' in text, group in ('success', 'monitoring', 'logs', 'log_error'))
                self.assertEqual('LOG_SCAN' in text, group in ('success', 'logs', 'log_error'))
                self.assertEqual('End-to-end smoke tests passed' in text, status == 0)
                self.assertEqual('Explicit Redis cache miss/hit path verified' in text,
                                 group not in ('gateway', 'qwen'))

    def test_join_before_monitoring_and_log_scan(self):
        cases = [('neither', 0), ('index', 37), ('qwen', 38), ('vector', 39), ('mcp', 40)]
        for timings, (group, status), prepared in product(('0', '1'), cases, (False, True)):
            if prepared and group == 'index':
                continue  # up.sh stops before smoke when its index fails.
            with self.subTest(timings=timings, group=group, prepared=prepared), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / 'scripts').mkdir()
                for script in ['smoke-test.sh', 'lib.sh']:
                    shutil.copy(ROOT / 'scripts' / script, root / 'scripts')
                (root / '.env').touch()
                (root / '.local').mkdir()
                (root / '.local/litellm-api-key').write_text('fixture')
                report = root / '.local/smoke/ingestion.json'
                report.parent.mkdir()
                report.write_text('stale')
                command = ['bash', 'scripts/smoke-test.sh']
                counts = '{"files_indexed": 2, "chunks_reused": 2}'
                if prepared:
                    (root / 'indexed').touch()
                    (root / 'retrieval-started').touch()
                    command.extend(['--index-result', counts])
                probes = root / 'probes.sh'
                probes.write_text(PROBES)
                log = root / 'output'
                # A regular file lets us observe the parent's exit immediately,
                # even if a regression leaves a child holding stdout open.
                with log.open('w') as output:
                    result = subprocess.run(
                        command, cwd=root, timeout=10,
                        env=os.environ | {'BASH_ENV': str(probes), 'FAIL_GROUP': group, 'TIMINGS': timings},
                        stdout=output, stderr=subprocess.STDOUT)
                text = log.read_text()
                self.assertEqual(result.returncode, status, text)
                self.assertEqual('INDEX_RAN' in text, not prepared)
                if prepared:
                    self.assertEqual(report.read_text().strip(), counts)
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
                    if lane == 'Indexing':
                        ran = not prepared
                    marker = f'[timings] {lane} '
                    self.assertEqual(marker in text, timings == '1' and ran, text)
                    if status == 0 and timings == '1' and ran:
                        self.assertLess(text.index(marker), text.index('MONITORING'))
                if status == 0:
                    for success in (qwen, 'VECTOR_DONE', mcp):
                        self.assertLess(text.index(success), text.index('MONITORING'))
                    self.assertGreater(text.index('LOG_SCAN'), text.index('MONITORING'))
