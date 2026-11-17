"""Missing or broken Cargo must fail host checks and the test launcher."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class RustRequirementsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'scripts').mkdir()
        shutil.copy(ROOT / 'Makefile', self.root / 'Makefile')
        for name in ('lib.sh', 'doctor.sh', 'test.sh'):
            shutil.copy(ROOT / 'scripts' / name, self.root / 'scripts' / name)
        self.binaries = self.root / 'bin'
        self.binaries.mkdir()
        # Exclude the host PATH so installed Cargo cannot hide a missing-tool bug.
        for name in ('awk', 'bash', 'dirname', 'make'):
            (self.binaries / name).symlink_to(shutil.which(name))
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
                          '    stream.write(json.dumps(["python", *sys.argv[1:]]) + "\\n")\n')
        python.chmod(0o755)

    def write_executable(self, relative_path, body):
        path = self.root / relative_path
        path.write_text('#!/bin/bash\n' + body)
        path.chmod(0o755)

    def install_cargo(self, version_status=0, test_status=0):
        self.write_executable('bin/cargo',
                              'printf \'["cargo","%s"]\\n\' "$*" >> "$TASK_EVENTS"\n'
                              f'if [[ "$1" == --version ]]; then exit {version_status}; fi\n'
                              f'exit {test_status}\n')

    def run_make(self, target):
        return subprocess.run([str(self.binaries / 'make'), target], cwd=self.root,
                              env=self.env, capture_output=True, text=True, timeout=15)

    def calls(self):
        if not self.events.exists():
            return []
        return [json.loads(line) for line in self.events.read_text().splitlines()]

    def test_test_target_rejects_missing_cargo_before_python_or_database_work(self):
        result = self.run_make('test')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('cargo', result.stderr.lower())
        self.assertEqual(self.calls(), [])

    def test_test_target_rejects_broken_cargo_before_python_or_database_work(self):
        self.install_cargo(version_status=7)
        result = self.run_make('test')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls(), [['cargo', '--version']])

    def test_test_target_runs_locked_rust_tests_and_propagates_failure(self):
        for test_status in (0, 11):
            with self.subTest(test_status=test_status):
                self.events.write_text('')
                self.install_cargo(test_status=test_status)
                result = self.run_make('test')
                self.assertEqual(result.returncode == 0, test_status == 0, result.stderr)
                self.assertIn(['cargo', 'test --locked --quiet --manifest-path '
                               'examples/rust-service/Cargo.toml'], self.calls())
                self.assertEqual('Unit tests passed' in result.stdout, test_status == 0)

    def test_doctor_rejects_missing_cargo_on_an_otherwise_healthy_host(self):
        result = self.run_make('doctor')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('cargo', result.stderr.lower())

    def test_doctor_checks_cargo_is_usable_and_rejects_broken_toolchains(self):
        for version_status in (0, 7):
            with self.subTest(version_status=version_status):
                self.events.write_text('')
                self.install_cargo(version_status=version_status)
                result = self.run_make('doctor')
                self.assertEqual(result.returncode == 0, version_status == 0, result.stderr)
                self.assertEqual(self.calls(), [['cargo', '--version']])
