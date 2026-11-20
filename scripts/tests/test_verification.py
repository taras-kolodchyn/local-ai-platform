"""Run the real verification target while controlling slow, external boundaries."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
PHASES = ['lint', 'test', 'up', 'status', 'smoke-test', 'moyai-smoke']
PRIVATE_OUTPUT = 'fixture request body and generated credential must stay private'

DRIVER = r'''import json, os, signal, subprocess, sys, time
from pathlib import Path
component, *args = sys.argv[1:]
if component == 'child':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path(os.environ['TASK_CHILD_READY']).write_text(str(os.getpid()))
    time.sleep(1.5)
    Path(os.environ['TASK_CHILD_MARKER']).write_text('operation continued')
    raise SystemExit(0)
state_file = Path(os.environ['TASK_STATE'])
state = json.loads(state_file.read_text())
settings = dict(line.split('=', 1) for line in Path('.env').read_text().splitlines() if '=' in line)
def reference(value):
    value = value.lower().removeprefix('docker.io/')
    return value if ':' in value.rsplit('/', 1)[-1] else value + ':latest'
with open(os.environ['TASK_EVENTS'], 'a') as stream:
    stream.write(json.dumps([component, *args]) + '\n')
error = 0
if component == 'phase':
    phase = args[0]
    print('fixture request body and generated credential must stay private')
    if phase == 'up':
        state['containers']['local-ai-platform'] = ['core-container']
        state['containers']['local-ai-moyai'] = ['moyai-container']
        state['loaded'] = [reference(settings['QWEN_MODEL']),
                           reference(settings['EMBEDDING_MODEL']), 'unrelated/other:latest']
    state_file.write_text(json.dumps(state))
    if os.getenv('BLOCK_PHASE') == phase:
        child = subprocess.Popen([sys.executable, __file__, 'child'])
        child.wait()
    raise SystemExit(21 if os.getenv('FAIL_PHASE') == phase else 0)
elif component == 'moyai':
    assert args == ['down'], args
    state['containers']['local-ai-moyai'] = []
elif component == 'docker' and args[0] == 'compose':
    assert 'down' in args and '--remove-orphans' in args, args
    assert os.environ['COMPOSE_PROJECT_NAME'] == 'local-ai-platform'
    assert not os.getenv('COMPOSE_PROFILES')
    if os.getenv('FAIL_DOWN'):
        error = 9
    elif not os.getenv('RESIDUAL_CONTAINER'):
        state['containers']['local-ai-platform'] = []
elif component == 'docker' and args[:2] == ['model', 'unload']:
    assert args[2:] == [settings['QWEN_MODEL'], settings['EMBEDDING_MODEL']], args
    if not os.getenv('RESIDUAL_MODEL'):
        state['loaded'] = ['unrelated/other:latest']
        if os.getenv('UNRELATED_SAME_NAME'):
            state['loaded'].append('another-owner/fixture-coder:v1')
elif component == 'docker' and args[0] == 'ps':
    assert '-a' in args or '--all' in args, 'Stopped containers must be checked too'
    assert '--format' in args, args
    project = next(arg.split('=', 2)[-1] for arg in args
                   if arg.startswith('label=com.docker.compose.project='))
    assert project in ('local-ai-platform', 'local-ai-moyai'), project
    if os.getenv('FAIL_INVENTORY') == 'containers':
        error = 10
    else:
        print('\n'.join(state['containers'][project]))
elif component == 'docker' and args == ['model', 'ps']:
    calls = [json.loads(line) for line in Path(os.environ['TASK_EVENTS']).read_text().splitlines()]
    if os.getenv('FAIL_INVENTORY') == 'models' and calls.count(['docker', 'model', 'ps']) > 1:
        error = 11
    elif os.getenv('MALFORMED_MODELS'):
        print('unexpected runtime response')
    else:
        print('MODEL NAME  BACKEND  MODE  UNTIL')
        for model in state['loaded']:
            if os.getenv('SHORT_MODEL_NAMES'):
                model = model.removeprefix('ai/')
            print(model + '  llama.cpp  completion  Forever')
            if 'embed' in model:
                print(model + '  llama.cpp  embedding  Forever')
else:
    raise SystemExit('Unexpected verification boundary: ' + repr([component, *args]))
state_file.write_text(json.dumps(state))
raise SystemExit(error)
'''


class VerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        scripts = self.root / 'scripts'
        scripts.mkdir()
        shutil.copy(ROOT / 'Makefile', self.root / 'Makefile')
        for name in ('verify.py', 'lib.sh', 'down.sh'):
            source = ROOT / 'scripts' / name
            if source.exists():
                shutil.copy(source, scripts / name)
        (self.root / '.env.example').write_text(
            'QWEN_MODEL=template-coder\nEMBEDDING_MODEL=template-embed\n')
        (self.root / '.env').write_text(
            'QWEN_MODEL=ai/fixture-coder:V1\nEMBEDDING_MODEL=ai/fixture-embed\n')
        self.driver = self.root / 'driver.py'
        self.driver.write_text(DRIVER)
        self.state_file = self.root / 'state.json'
        self.initial_state = {
            'containers': {'local-ai-platform': ['existing-core'],
                           'local-ai-moyai': ['existing-moyai'],
                           'unrelated-project': ['unrelated-container']},
            'loaded': ['docker.io/ai/fixture-coder:v1', 'ai/fixture-embed:latest',
                       'unrelated/other:latest'],
        }
        self.state_file.write_text(json.dumps(self.initial_state))
        self.events_file = self.root / 'events.jsonl'
        self.events_file.touch()
        binaries = self.root / 'bin'
        binaries.mkdir()
        docker = binaries / 'docker'
        docker.write_text('#!/bin/sh\nexec "$TASK_PYTHON" "$TASK_DRIVER" docker "$@"\n')
        docker.chmod(0o755)
        (scripts / 'moyai.sh').write_text(
            '#!/bin/sh\nexec "$TASK_PYTHON" "$TASK_DRIVER" moyai "$@"\n')
        for phase in PHASES[:-1]:
            (scripts / (phase + '.sh')).write_text(
                '#!/bin/sh\nexec "$TASK_PYTHON" "$TASK_DRIVER" phase ' + phase + ' "$@"\n')
        (scripts / 'status.sh').write_text(
            '#!/bin/sh\nexec "$TASK_PYTHON" "$TASK_DRIVER" phase status "$FORMAT"\n')
        (scripts / 'moyai-smoke.py').write_text(
            'import os, sys\nos.execv(os.environ["TASK_PYTHON"], '
            '[os.environ["TASK_PYTHON"], os.environ["TASK_DRIVER"], '
            '"phase", "moyai-smoke", *sys.argv[1:]])\n')
        (scripts / 'repository-smoke.py').write_text(
            'import os, sys\nos.execv(os.environ["TASK_PYTHON"], '
            '[os.environ["TASK_PYTHON"], os.environ["TASK_DRIVER"], '
            '"phase", "repository-smoke", *sys.argv[1:]])\n')
        self.env = os.environ | {
            'PATH': str(binaries) + os.pathsep + os.environ['PATH'],
            'TASK_PYTHON': sys.executable, 'TASK_DRIVER': str(self.driver),
            'TASK_STATE': str(self.state_file), 'TASK_EVENTS': str(self.events_file),
            'TASK_CHILD_READY': str(self.root / 'child-ready'),
            'TASK_CHILD_MARKER': str(self.root / 'child-marker'),
            # Shell settings must not replace the configuration or Compose scope.
            'QWEN_MODEL': 'unrelated/other:latest',
            'EMBEDDING_MODEL': 'unrelated/other:latest',
            'COMPOSE_PROJECT_NAME': 'unrelated-project',
            'COMPOSE_FILE': '/tmp/unrelated.yaml', 'COMPOSE_PROFILES': 'connected',
        }

    def run_verify(self, *arguments, **overrides):
        shutil.rmtree(self.root / '.local' / 'verification', ignore_errors=True)
        self.events_file.write_text('')
        self.state_file.write_text(json.dumps(self.initial_state))
        return subprocess.run(['make', '--no-print-directory', 'verify', *arguments], cwd=self.root,
                              env=self.env | overrides, text=True, capture_output=True,
                              timeout=20)

    def calls(self):
        return [json.loads(line) for line in self.events_file.read_text().splitlines()]

    def result(self):
        paths = list((self.root / '.local' / 'verification').glob('*/result.json'))
        self.assertEqual(len(paths), 1, paths)
        return paths[0], json.loads(paths[0].read_text())

    def assert_stopped(self):
        state = json.loads(self.state_file.read_text())
        self.assertEqual(state['containers']['local-ai-platform'], [])
        self.assertEqual(state['containers']['local-ai-moyai'], [])
        self.assertEqual(state['containers']['unrelated-project'], ['unrelated-container'])
        self.assertEqual(state['loaded'], ['unrelated/other:latest'])

    def test_success_runs_required_targets_in_order_then_checks_stopped_inventory(self):
        result = self.run_verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        self.assertEqual([call[1] for call in calls if call[0] == 'phase'], PHASES)
        self.assertIn(['phase', 'moyai-smoke', '--restart'], calls)
        self.assertIn(['phase', 'status', 'json'], calls)
        down = calls.index(['moyai', 'down'])
        self.assertLess(calls.index(['phase', 'moyai-smoke', '--restart']), down)
        self.assertGreater(calls.index(['docker', 'model', 'ps'], down + 1), down)
        self.assert_stopped()
        path, report = self.result()
        self.assertEqual(report['status'], 'passed')
        self.assertIsNone(report['failed_phase'])
        self.assertEqual([phase['name'] for phase in report['phases']], PHASES + ['down'])
        self.assertTrue(report['inventory']['passed'])
        self.assertIn(str(path), result.stdout)

    def test_external_repo_adds_repository_acceptance_before_shutdown(self):
        source = self.root / "developer's source"
        source.mkdir()
        for fail in (False, True):
            with self.subTest(repository_failure=fail):
                result = self.run_verify('REPO=' + str(source), FAIL_PHASE='repository-smoke' if fail else '')
                self.assertEqual(result.returncode == 0, not fail, result.stdout + result.stderr)
                calls = self.calls()
                phases = [call for call in calls if call[0] == 'phase']
                self.assertEqual([call[1] for call in phases], PHASES + ['repository-smoke'])
                argv = phases[-1][2:]
                self.assertEqual(argv[argv.index('--repo') + 1], str(source))
                self.assertIn('--restart', argv)
                self.assertLess(calls.index(phases[-1]), calls.index(['moyai', 'down']))
                self.assert_stopped()
                _, report = self.result()
                self.assertEqual(report['failed_phase'], 'repository-smoke' if fail else None)
                self.assertEqual(report['status'], 'failed' if fail else 'passed')

    def test_phase_failure_stops_later_work_but_always_runs_supported_shutdown(self):
        for phase in PHASES:
            with self.subTest(phase=phase):
                result = self.run_verify(FAIL_PHASE=phase)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual([call[1] for call in self.calls() if call[0] == 'phase'],
                                 PHASES[:PHASES.index(phase) + 1])
                self.assertIn(['moyai', 'down'], self.calls())
                self.assert_stopped()
                _, report = self.result()
                self.assertEqual(report['failed_phase'], phase)
                self.assertEqual(report['status'], 'failed')
                self.assertTrue(report['inventory']['passed'])
                self.assertNotEqual(report['phases'][PHASES.index(phase)]['returncode'], 0)

    def test_cleanup_failure_is_fatal_and_does_not_hide_the_original_failed_phase(self):
        for original in ('', 'test'):
            with self.subTest(original=original):
                result = self.run_verify(FAIL_DOWN='1', FAIL_PHASE=original)
                self.assertNotEqual(result.returncode, 0)
                _, report = self.result()
                self.assertEqual(report['failed_phase'], original or 'down')
                self.assertNotEqual(report['phases'][-1]['returncode'], 0)
                self.assertFalse(report['inventory']['passed'])
                self.assertIn('down', result.stdout)

    def test_successful_cleanup_requests_are_not_proof_of_empty_owned_inventory(self):
        for residual in ('RESIDUAL_CONTAINER', 'RESIDUAL_MODEL'):
            with self.subTest(residual=residual):
                result = self.run_verify(**{residual: '1'})
                self.assertNotEqual(result.returncode, 0)
                _, report = self.result()
                self.assertEqual(report['failed_phase'], 'stopped-state')
                self.assertEqual(report['phases'][-1]['returncode'], 0)
                self.assertFalse(report['inventory']['passed'])
                if residual == 'RESIDUAL_MODEL':
                    self.assertEqual(set(report['inventory']['resident_configured_models']),
                                     {'ai/fixture-coder:v1', 'ai/fixture-embed:latest'})
                    self.assertEqual(len(report['inventory']['resident_configured_models']), 2)
                else:
                    self.assertEqual(report['inventory']['containers']['local-ai-platform'],
                                     ['core-container'])

    def test_unavailable_or_malformed_final_inventory_fails_closed(self):
        for overrides in ({'FAIL_INVENTORY': 'containers'}, {'FAIL_INVENTORY': 'models'},
                          {'MALFORMED_MODELS': '1'}):
            with self.subTest(overrides=overrides):
                result = self.run_verify(**overrides)
                self.assertNotEqual(result.returncode, 0)
                _, report = self.result()
                self.assertEqual(report['failed_phase'], 'stopped-state')
                self.assertTrue(report['inventory']['errors'])

    def test_actual_dmr_short_names_cannot_hide_resident_configured_weights(self):
        (self.root / '.env').write_text(
            'QWEN_MODEL=ai/qwen3-coder:30B-A3B-UD-Q4_K_XL\n'
            'EMBEDDING_MODEL=docker.io/ai/qwen3-embedding:0.6B-F16\n')
        result = self.run_verify(RESIDUAL_MODEL='1', SHORT_MODEL_NAMES='1')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        _, report = self.result()
        self.assertEqual(report['failed_phase'], 'stopped-state')
        self.assertFalse(report['inventory']['passed'])
        self.assertEqual(report['inventory']['resident_configured_models'],
                         ['ai/qwen3-coder:30b-a3b-ud-q4_k_xl',
                          'ai/qwen3-embedding:0.6b-f16'])

    def test_full_unrelated_namespace_with_same_basename_does_not_fail_verification(self):
        result = self.run_verify(UNRELATED_SAME_NAME='1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        _, report = self.result()
        self.assertTrue(report['inventory']['passed'])
        self.assertIn('another-owner/fixture-coder:v1',
                      json.loads(self.state_file.read_text())['loaded'])

    def test_phase_output_and_reports_are_private_and_stdout_contains_only_metadata(self):
        result = self.run_verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(PRIVATE_OUTPUT, result.stdout + result.stderr)
        path, report = self.result()
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(path.parent.parent.stat().st_mode & 0o777, 0o700)
        for artifact in path.parent.iterdir():
            self.assertEqual(artifact.stat().st_mode & 0o077, 0, artifact)
        self.assertIn(PRIVATE_OUTPUT, Path(report['phases'][0]['log']).read_text())

    def test_closed_output_consumer_does_not_prevent_supported_cleanup(self):
        process = subprocess.Popen(['make', '--no-print-directory', 'verify'], cwd=self.root,
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.stdout.close()
        process.stdout = None
        _, stderr = process.communicate(timeout=20)
        self.assert_stopped()
        self.assertEqual(process.returncode, 0, stderr)
        _, report = self.result()
        self.assertEqual(report['status'], 'passed')

    def test_signal_stops_the_active_child_operation_then_cleans_up(self):
        process = subprocess.Popen(['make', '--no-print-directory', 'verify'], cwd=self.root,
                                   env=self.env | {'BLOCK_PHASE': 'up'}, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.monotonic() + 8
        ready = self.root / 'child-ready'
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(ready.exists(), process.communicate(timeout=1) if process.poll() is not None else '')
        os.killpg(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        self.assertNotEqual(process.returncode, 0, stdout + stderr)
        # A terminal signal can terminate outer make before the runner finishes cleanup.
        deadline = time.monotonic() + 5
        while not list((self.root / '.local' / 'verification').glob('*/result.json')) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assert_stopped()
        _, report = self.result()
        self.assertEqual(report['signal'], signal.SIGTERM)
        self.assertEqual(report['failed_phase'], 'up')
        self.assertEqual([call[1] for call in self.calls() if call[0] == 'phase'],
                         PHASES[:3])
        time.sleep(1.6)
        self.assertFalse((self.root / 'child-marker').exists(), 'The interrupted child kept running')


if __name__ == '__main__':
    unittest.main()
