"""Exercise the supported host prerequisites and test suites through Make."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class HostRequirementsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'scripts').mkdir()
        shutil.copy(ROOT / 'Makefile', self.root / 'Makefile')
        for name in ('lib.sh', 'doctor.sh', 'host-ports.sh', 'test.sh', 'model-config.py'):
            shutil.copy(ROOT / 'scripts' / name, self.root / 'scripts' / name)
        shutil.copy(ROOT / '.env.example', self.root / '.env.example')
        self.binaries = self.root / 'bin'
        self.binaries.mkdir()
        # Keep the host PATH out of both targets, including any installed Rust toolchain.
        for name in ('awk', 'bash', 'dirname', 'make'):
            (self.binaries / name).symlink_to(shutil.which(name))
        (self.binaries / 'python3').symlink_to(sys.executable)
        self.events = self.root / 'events.jsonl'
        self.env = os.environ | {'PATH': str(self.binaries), 'TASK_EVENTS': str(self.events)}
        self.write_executable('bin/uname', 'case "$1" in -s) printf "Darwin\\n";; '
                              '-m) printf "arm64\\n";; *) exit 1;; esac\n')
        self.write_executable('bin/sysctl', 'printf "51539607552\\n"\n')
        self.write_executable('bin/df', 'printf "Filesystem Blocks Used Available Capacity Mounted\\n'
                              'fixture 999999999 0 999999999 0%% /\\n"\n')
        self.write_executable('bin/lsof', 'exit 1\n')
        for name in ('docker', 'curl', 'jq', 'git', 'openssl'):
            self.write_executable('bin/' + name, 'exit 0\n')
        python = self.root / '.local/test-venv/bin/python'
        self.write_executable('scripts/test-python.sh',
                              'printf \'["python-selection"]\\n\' >> "$TASK_EVENTS"\n'
                              'printf "%s\\n" "$PWD/.local/test-venv/bin/python"\n')
        python.parent.mkdir(parents=True)
        python.write_text(f'#!{sys.executable}\nimport json, os, sys\n'
                          'with open(os.environ["TASK_EVENTS"], "a") as stream:\n'
                          '    stream.write(json.dumps(["python", *sys.argv[1:]]) + "\\n")\n'
                          'if os.getenv("FAIL_SUITE") in sys.argv[1:]:\n'
                          '    sys.exit(9)\n')
        python.chmod(0o755)

    def write_executable(self, relative_path, body):
        path = self.root / relative_path
        path.write_text('#!/bin/bash\n' + body)
        path.chmod(0o755)

    def run_make(self, target):
        return subprocess.run([str(self.binaries / 'make'), target], cwd=self.root,
                              env=self.env, capture_output=True, text=True, timeout=15)

    def calls(self):
        if not self.events.exists():
            return []
        return [json.loads(line) for line in self.events.read_text().splitlines()]

    def test_platform_suites_run_without_host_cargo(self):
        result = self.run_make('test')
        self.assertEqual(result.returncode, 0, result.stderr)
        for suite in ('retrieval/tests', 'mcp/offline/tests', 'execution/tests', 'scripts/tests'):
            self.assertTrue(any(suite in args for args in self.calls()), suite)

    def test_required_database_suite_failure_stops_the_launcher(self):
        self.env['FAIL_SUITE'] = 'execution/tests'
        result = self.run_make('test')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Unit tests passed', result.stdout)
        self.assertFalse(any('scripts/tests' in args for args in self.calls()))

    def test_doctor_accepts_a_healthy_host_without_cargo(self):
        result = self.run_make('doctor')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_doctor_and_smoke_take_separate_listener_snapshots(self):
        (self.binaries / 'grep').symlink_to(shutil.which('grep'))
        self.write_executable('bin/lsof', r'''
printf '%s\n' "$*" >> calls
printf '%s\n' "$LISTENERS"
exit 1
''')
        self.env['LISTENERS'] = 'p42\nn127.0.0.1:3000\nn[::1]:8000'
        doctor = self.run_make('doctor')
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        self.assertIn('Port 3000 is already in use', doctor.stdout)
        self.assertIn('Port 4000 available', doctor.stdout)

        ports = (3000, 4000, 8000, 8001, 9090, 12434)
        local = ['n[::1]:' + str(port) for port in ports]
        cases = [
            (local + ['n127.0.0.1:4000'], None),
            (local[1:] + ['n127.0.0.1:13000'], 'missing on port 3000'),
            *[(local + [f'n{address}:8001'], 'exposed beyond loopback')
              for address in ('*', '0.0.0.0', '[::]')],
        ]
        for endpoints, error in cases:
            with self.subTest(endpoints=endpoints):
                self.env['LISTENERS'] = '\n'.join(endpoints)
                result = subprocess.run(['bash', 'scripts/host-ports.sh', 'verify'], cwd=self.root,
                                        env=self.env, capture_output=True, text=True, timeout=5)
                if error:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(error, result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'calls').read_text().splitlines(),
                         ['-nP -iTCP:3000,4000,8000,8001,9090,12434 -sTCP:LISTEN -Fn'] * (1 + len(cases)))

    def test_pulls_overlap_and_both_must_finish_before_configuration(self):
        shutil.copy(ROOT / 'scripts/pull-model.sh', self.root / 'scripts')
        self.write_executable('scripts/ensure-docker.sh', 'exit 0\n')
        (self.root / '.env').write_text('QWEN_MODEL=coder\nEMBEDDING_MODEL=embed\n')
        for name in ('seq', 'sleep'):
            (self.binaries / name).symlink_to(shutil.which(name))
        for name in ('pull-reranker.py', 'model-config.py'):
            (self.root / 'scripts' / name).write_text(
                'from pathlib import Path\n'
                'assert Path("coder.done").exists() and Path("embed.done").exists()\n'
                'Path(__file__).with_suffix(".called").touch()\n')
        self.write_executable('bin/docker', '''
if [[ "$1 $2" == "model pull" ]]; then
  : > "$3.started"
  for ((i=0; i<200; i++)); do
    [[ -f coder.started && -f embed.started ]] && break
    sleep 0.01
  done
  [[ -f coder.started && -f embed.started ]] || exit 42
  : > "$3.done"
  [[ "$FAIL_MODEL" != "$3" ]] || exit 23
elif [[ "$1 $2" == "model configure" ]]; then
  : > configured
fi
''')
        for failure in ('coder', 'embed', ''):
            with self.subTest(failure=failure):
                result = subprocess.run(['bash', 'scripts/pull-model.sh'], cwd=self.root,
                                        env=self.env | {'FAIL_MODEL': failure}, capture_output=True,
                                        text=True, timeout=10)
                self.assertEqual(result.returncode, 23 if failure else 0, result.stderr)
                for model in ('coder', 'embed'):
                    (self.root / f'{model}.done').unlink()
                    (self.root / f'{model}.started').unlink()
                for marker in ('configured', 'scripts/pull-reranker.called', 'scripts/model-config.called'):
                    self.assertEqual((self.root / marker).exists(), not failure, marker)
