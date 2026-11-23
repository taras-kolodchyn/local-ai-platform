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
        self.write_executable('bin/docker', '''printf '%s\\n' "$*" >> docker.calls''')
        result = self.run_make('doctor')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / 'docker.calls').read_text().splitlines()
        for probe in ('info', 'model status', 'compose version', 'model version'):
            self.assertEqual(calls.count(probe), 1, probe)

        (self.root / 'docker.calls').unlink()
        result = subprocess.run(['bash', 'scripts/doctor.sh', '--runtime-checked'], cwd=self.root,
                                env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'docker.calls').read_text().splitlines(),
                         [call for call in calls if call.startswith('model inspect ')])

    def test_doctor_accepts_a_healthy_linux_host(self):
        # 48 GiB in KiB, matching the Darwin fixture's byte total.
        self.write_executable('bin/uname', 'case "$1" in -s) printf "Linux\\n";; '
                              '-m) printf "x86_64\\n";; *) exit 1;; esac\n')
        self.write_executable('bin/free', 'printf "Mem: 50331648 0 0\\n"\n')
        result = self.run_make('doctor')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Linux detected', result.stdout)
        self.assertIn('System memory: 48 GiB', result.stdout)

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

    def test_verify_reads_published_ports_through_ss(self):
        # On rootful Docker Engine an ordinary user's lsof cannot see the
        # root-owned docker-proxy listeners, so verify must enumerate via ss,
        # which reads the kernel socket table and reveals the bind address.
        (self.binaries / 'grep').symlink_to(shutil.which('grep'))
        self.write_executable('bin/ss', r'''
printf '%s\n' "$*" >> ss_calls
while IFS= read -r endpoint; do
  [ -n "$endpoint" ] && printf 'LISTEN 0 4096 %s *:*\n' "$endpoint"
done <<< "$LISTENERS"
''')
        self.write_executable('bin/lsof', 'printf lsof >> lsof_calls\nexit 1\n')

        ports = (3000, 4000, 8000, 8001, 9090, 12434)
        loopback = ['127.0.0.1:' + str(port) for port in ports]
        self.write_executable('bin/uname', 'printf "Linux\n"')
        self.write_executable('bin/docker', '[[ "$1 $2 $3" == "network inspect bridge" ]] || exit 1\n'
                              'printf "172.18.0.1\n"')
        cases = [
            (loopback + ['172.18.0.1:12434'], None),
            (loopback + ['192.0.2.10:12434'], 'exposed beyond loopback'),
            (loopback + ['172.18.0.1:8001'], 'exposed beyond loopback'),
            (loopback, None),
            (loopback[1:], 'missing on port 3000'),
            *[(loopback + [f'{address}:8001'], 'exposed beyond loopback')
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
        # ss is preferred; the root-blind lsof probe must never run.
        self.assertEqual((self.root / 'ss_calls').read_text().splitlines(),
                         ['-H -l -t -n'] * len(cases))
        self.assertFalse((self.root / 'lsof_calls').exists())

    def test_pulls_overlap_and_both_must_finish_before_configuration(self):
        shutil.copy(ROOT / 'scripts/pull-model.sh', self.root / 'scripts')
        self.write_executable('scripts/ensure-docker.sh', 'exit 0\n')
    def setup_model_preparation(self):
        for script in ('pull-model.sh', 'ensure-docker.sh'):
            shutil.copy(ROOT / 'scripts' / script, self.root / 'scripts')
        self.write_executable('scripts/bootstrap.sh', 'exit 0')
        (self.root / '.env').write_text('QWEN_MODEL=coder\nEMBEDDING_MODEL=embed\n')
        self.write_executable('bin/sleep', """printf 'sleep %s\\n' "$1" >> readiness""")
        # A barrier makes skipped or serialized workers fail deterministically.
        (self.root / 'worker.py').write_text("""
import os, sys, time
from pathlib import Path
state = Path(os.environ['STATE'])
name = sys.argv[1]
(state / (name + '.started')).touch()
workers = os.environ['WORKERS'].split()
deadline = time.monotonic() + 3
while not all((state / (peer + '.started')).exists() for peer in workers):
    if time.monotonic() >= deadline:
        sys.exit('worker barrier timed out: ' + name)
    time.sleep(0.005)
(state / (name + '.done')).touch()
sys.exit(23 if name == os.getenv('FAIL') else 0)
""")
        (self.root / 'scripts/pull-reranker.py').write_text(
            'import runpy, sys\nsys.argv[1:] = ["reranker"]\nrunpy.run_path("worker.py")\n')
        (self.root / 'scripts/model-config.py').write_text(
            'from pathlib import Path\nassert Path("configured").exists()\nPath("runtime-env").touch()\n')
        self.write_executable('bin/docker', """
case "$1 ${2:-}" in
  'info ') echo info >> readiness ;;
  'desktop enable') echo enable >> readiness ;;
  'model status')
    echo status >> readiness
    n=0
    [[ ! -e attempts ]] || read -r n < attempts
    echo "$((n+1))" > attempts
    (( n >= ${UNREADY:-0} )) ;;
  'model pull') exec python3 worker.py "$3" ;;
  'compose version') exec python3 worker.py compose ;;
  'model version') exec python3 worker.py dmr ;;
  'model configure')
    for worker in $WORKERS; do [[ -e "$STATE/$worker.done" ]] || exit 42; done
    : > configured ;;
  *) exit 2 ;;
esac
""")

    def test_startup_workers_overlap_and_each_failure_blocks_configuration(self):
        self.setup_model_preparation()
        workers = ['coder', 'embed', 'reranker', 'compose', 'dmr']
        for failure in ['', *workers]:
            with self.subTest(failure=failure):
                state = self.root / (failure or 'success')
                state.mkdir()
                result = subprocess.run(
                    ['bash', 'scripts/pull-model.sh', '--for-up'], cwd=self.root,
                    env=self.env | {'STATE': str(state), 'WORKERS': ' '.join(workers), 'FAIL': failure},
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode == 0, not failure, result.stderr)
                self.assertEqual((self.root / 'readiness').read_text().splitlines(), ['enable', 'status'])
                (self.root / 'readiness').unlink()
                for worker in workers:
                    self.assertTrue((state / (worker + '.done')).exists(), worker)
                for marker in ('configured', 'runtime-env'):
                    path = self.root / marker
                    self.assertEqual(path.exists(), not failure, marker)
                    path.unlink(missing_ok=True)

    def test_standalone_pull_waits_for_docker_and_runner_and_honors_deadline(self):
        self.setup_model_preparation()
        self.env.update(STATE=str(self.root), WORKERS='coder embed reranker', UNREADY='60')
        result = self.run_make('pull-model')
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = ['info', 'enable'] + ['status', 'sleep 2'] * 60 + ['status']
        self.assertEqual((self.root / 'readiness').read_text().splitlines(), expected)
        self.assertTrue((self.root / 'runtime-env').exists())
        for marker in ('readiness', 'attempts', 'runtime-env', 'configured'):
            (self.root / marker).unlink()
        for marker in self.root.glob('*.started'):
            marker.unlink()

        self.env['UNREADY'] = '61'
        result = self.run_make('pull-model')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Docker Model Runner did not become ready', result.stderr)
        self.assertEqual((self.root / 'readiness').read_text().splitlines(), expected)
        self.assertFalse(list(self.root.glob('*.started')))
        self.assertFalse((self.root / 'configured').exists())
