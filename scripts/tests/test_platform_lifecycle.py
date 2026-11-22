"""Exercise the real Makefile and lifecycle scripts with controlled Docker boundaries."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
INDEX = ['dependency', 'scripts/retrieval_probe.py', 'index']
INDEX_RESULT = '{"files_indexed": 2, "chunks_reused": 2}'
SMOKE = ['dependency', 'smoke-test.sh', '--index-result', INDEX_RESULT]

DRIVER = '''import json, os, sys, time
from pathlib import Path
state_file = Path(os.environ['TASK_STATE'])
component, *args = sys.argv[1:]
with open(os.environ['TASK_EVENTS'], 'a') as stream:
    stream.write(json.dumps([component, *args]) + '\\n')

def rendezvous(worker, peer):
    if os.getenv('TEST_JOIN'):
        Path(worker + '.started').touch()
        deadline = time.monotonic() + 3
        while not Path(peer + '.started').exists():
            if time.monotonic() > deadline:
                raise SystemExit('Workers did not overlap: ' + worker)
            time.sleep(.01)
        if peer == os.getenv('FAIL_GROUP'):
            time.sleep(.15)
        Path(worker + '.finished').touch()
        if worker == os.getenv('FAIL_GROUP'):
            raise SystemExit(11)

if component == 'dependency' and args == ['scripts/retrieval_probe.py', 'index']:
    rendezvous('index', 'moyai')
    print(os.environ['TASK_INDEX_RESULT'])
    sys.exit(0)
state = json.loads(state_file.read_text())
error = 0
if component == 'moyai':
    if args == ['up']:
        rendezvous('moyai', 'index')
    if os.getenv('FAIL_MOYAI'):
        error = 6
    else:
        if args == ['up'] and not state['core']:
            raise SystemExit('Core gateway must start before Moyai')
        state['moyai'] = args == ['up']
elif component == 'dependency':
    if args[:1] == ['smoke-test.sh'] and os.getenv('FAIL_GROUP') == 'smoke':
        error = 12
    if args == ['scripts/reconcile-agents.py', '--stop-runtimes']:
        state['workspace'] = False
elif component == 'docker' and args[0] == 'compose':
    if os.getenv('COMPOSE_PROJECT_NAME', 'local-ai-platform') != 'local-ai-platform' or os.getenv('COMPOSE_FILE', '').endswith('unrelated.yaml'):
        state['unrelated_project'] = 'down' not in args
    elif 'stop' in args:
        if 'workspace-development' in args and 'workspace-review' in args and 'workspace-research' in args:
            state['workspace'] = False
    elif 'down' in args:
        if os.getenv('FAIL_CORE'):
            error = 7
        else:
            state['core'] = False
            if '--profile' in args and args[args.index('--profile') + 1] == '*':
                state['workspace'] = state['connected'] = False
            if '--volumes' in args:
                state['volumes'] = []
    elif 'exec' in args and 'cmp' in args:
        if os.getenv('STALE_MOUNT') in args:
            error = 1
    elif 'exec' in args and 'psql' in args:
        print('t' if not os.getenv('INVALID_MCP_ROLE') else 'f')
    elif 'up' in args:
        state['core'] = True
        state['gateway_models'] = [os.getenv('QWEN_MODEL'), os.getenv('EMBEDDING_MODEL')]
        state['loaded'] = list(dict.fromkeys(state['loaded'] + state['gateway_models']))
elif component == 'docker' and args[:2] == ['model', 'unload']:
    if os.getenv('FAIL_UNLOAD') or any(state[k] for k in ('core', 'workspace', 'connected', 'moyai')):
        error = 9
    else:
        state['loaded'] = [] if '--all' in args else [m for m in state['loaded'] if m not in args[2:]]
elif component == 'docker' and args == ['model', 'ps']:
    print('MODEL NAME BACKEND MODE UNTIL')
    for model in state['loaded']:
        print(model + ' llama.cpp completion idle')
else:
    raise SystemExit('Unexpected lifecycle command')
state_file.write_text(json.dumps(state))
raise SystemExit(error)
'''


class PlatformLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.scripts = self.root / 'scripts'
        self.scripts.mkdir()
        (self.root / '.local').mkdir()
        for name in ('litellm-api-key', 'vector-backend-key'):
            (self.root / '.local' / name).write_text('fixture-key')
        shutil.copy(ROOT / 'Makefile', self.root / 'Makefile')
        for name in ('lib.sh', 'up.sh', 'down.sh', 'compose.sh', 'compose-timings.py', 'mcp-provision.sh'):
            if (ROOT / 'scripts' / name).exists():
                shutil.copy(ROOT / 'scripts' / name, self.scripts / name)
        (self.root / 'postgres').mkdir()
        shutil.copy(ROOT / 'postgres/check-mcp-role.sql', self.root / 'postgres')
        (self.root / '.env').write_text('MCP_POSTGRES_PASSWORD=fixture\nQWEN_MODEL=fixture-coder\nEMBEDDING_MODEL=fixture-embed\n')
        (self.root / '.env.example').write_text('QWEN_MODEL=template-coder\nEMBEDDING_MODEL=template-embed\n')
        driver = self.root / 'driver.py'
        driver.write_text(DRIVER)
        self.state_file = self.root / 'state.json'
        self.events = self.root / 'events.jsonl'
        self.initial = {'core': True, 'workspace': True, 'connected': True, 'moyai': True,
                        'unrelated_project': True,
                        'loaded': ['fixture-coder', 'fixture-embed', 'unrelated-model'],
                        'volumes': ['retained-platform-data', 'retained-moyai-data']}
        self.state_file.write_text(json.dumps(self.initial))
        binaries = self.root / 'bin'
        binaries.mkdir()
        docker = binaries / 'docker'
        docker.write_text('#!/bin/sh\nexec "$TASK_PYTHON" "$TASK_DRIVER" docker "$@"\n')
        docker.chmod(0o755)
        python = binaries / 'python3'
        python.write_text('#!/bin/sh\nif [ "$1" = scripts/compose-timings.py ]; then exec "$TASK_PYTHON" "$@"; fi\n'
                          'exec "$TASK_PYTHON" "$TASK_DRIVER" dependency "$@"\n')
        python.chmod(0o755)
        (self.scripts / 'moyai.sh').write_text('#!/bin/sh\nexec "$TASK_PYTHON" "$TASK_DRIVER" moyai "$@"\n')
        for name in ('bootstrap.sh', 'ensure-docker.sh', 'pull-model.sh', 'doctor.sh',
                     'provision-client-key.sh',
                     'smoke-test.sh',):
            (self.scripts / name).write_text(
                '#!/bin/sh\nexec "$TASK_PYTHON" "$TASK_DRIVER" dependency ' + name + ' "$@"\n')
        self.env = os.environ | {'PATH': str(binaries) + os.pathsep + os.environ['PATH'],
                    'TASK_PYTHON': os.sys.executable, 'TASK_DRIVER': str(driver),
                    'TASK_STATE': str(self.state_file), 'TASK_EVENTS': str(self.events),
                    'TASK_INDEX_RESULT': INDEX_RESULT}

    def run_make(self, target, timings='0', **overrides):
        return subprocess.run(['make', target, 'TIMINGS=' + timings], cwd=self.root, env=self.env | overrides,
                              capture_output=True, text=True, timeout=15)

    def calls(self):
        return [json.loads(line) for line in self.events.read_text().splitlines()]

    def test_up_starts_only_moyai_then_verifies_the_complete_stack(self):
        self.events.write_text('')
        self.state_file.write_text(json.dumps(self.initial | {'core': False, 'moyai': False}))
        result = self.run_make('up')
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.state_file.read_text())
        self.assertTrue(state['core'] and state['moyai'])
        calls = self.calls()
        runtime_scripts = {'ensure-docker.sh', 'pull-model.sh', 'doctor.sh'}
        self.assertEqual([call[1:] for call in calls if call[0] == 'dependency' and call[1] in runtime_scripts],
                         [['ensure-docker.sh'], ['pull-model.sh', '--for-up'], ['doctor.sh', '--runtime-checked']])
        self.assertEqual(calls.count(['moyai', 'up']), 1)
        core_ready, = [call for call in calls if call[:2] == ['docker', 'compose'] and '--wait' in call]
        self.assertLess(calls.index(core_ready), calls.index(['dependency', 'provision-client-key.sh']))
        for group in (INDEX, ['moyai', 'up']):
            self.assertLess(calls.index(['dependency', 'provision-client-key.sh']), calls.index(group))
        self.assertLess(calls.index(['dependency', 'scripts/provision-workspace.py', 'moyai-skills']),
                        calls.index(['moyai', 'up']))
        self.assertFalse(state['workspace'])
        self.assertLess(calls.index(['moyai', 'up']), calls.index(SMOKE))

    def test_up_joins_moyai_and_current_index_before_smoke(self):
        for timings in ('0', '1'):
            for failure in ('', 'index', 'moyai', 'smoke'):
                with self.subTest(timings=timings, failure=failure):
                    self.events.write_text('')
                    for marker in ('index.started', 'moyai.started', 'index.finished', 'moyai.finished'):
                        (self.root / marker).unlink(missing_ok=True)
                    # Use a file so an orphaned child cannot keep capture_output's pipe open.
                    with (self.root / 'output').open('w') as out:
                        result = subprocess.run(['make', 'up', 'TIMINGS=' + timings], cwd=self.root,
                                                env=self.env | {'TEST_JOIN': '1', 'FAIL_GROUP': failure},
                                                stdout=out, stderr=subprocess.STDOUT, timeout=15)
                    self.assertEqual(result.returncode == 0, not failure, (self.root / 'output').read_text())
                    self.assertTrue((self.root / 'index.finished').exists())
                    self.assertTrue((self.root / 'moyai.finished').exists())
                    calls = self.calls()
                    self.assertEqual(calls.count(INDEX), 1)
                    self.assertEqual([c for c in calls if c[:2] == SMOKE[:2]],
                                     [SMOKE] if failure in ('', 'smoke') else [])

    def test_repeat_up_only_reconciles_core_once_and_runs_smoke(self):
        for timings in ('0', '1'):
            self.events.write_text('')
            result = self.run_make('up', timings=timings)
            self.assertEqual(result.returncode, 0, result.stderr)
            progress = ['--progress', 'json'] if timings == '1' else []
            up = [c for c in self.calls() if c[:2] == ['docker', 'compose'] and 'up' in c]
            self.assertEqual('[timings] make up' in result.stderr, timings == '1')
            self.assertLess(self.calls().index(['dependency', 'scripts/build-images.py']), self.calls().index(up[0]))
            self.assertEqual(up, [
                ['docker', 'compose', *progress, 'up', '--detach', '--no-build', 'postgres'],
                ['docker', 'compose', *progress, '-f', str(self.root / 'compose.yaml'),
                 '-f', str(self.root / 'compose.mcp-current.yaml'),
                 'up', '--detach', '--no-build', '--wait', '--wait-timeout', '420'],
            ])
            self.assertNotIn(['docker', 'compose', 'restart', 'retrieval'], self.calls())
            self.assertIn(SMOKE, self.calls())

    def test_up_stops_before_smoke_if_mcp_role_repair_did_not_converge(self):
        result = self.run_make('up', INVALID_MCP_ROLE='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('after provisioning', result.stderr)
        self.assertNotIn(INDEX, self.calls())
        self.assertFalse(any(c[:2] == SMOKE[:2] for c in self.calls()))

    def test_up_rebinds_stale_retrieval_key_after_provisioning(self):
        for mount in ('litellm_client_key', 'vector_backend_key'):
            with self.subTest(mount=mount):
                self.events.write_text('')
                result = self.run_make('up', STALE_MOUNT='/run/secrets/' + mount)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = self.calls()
                refresh, = [c for c in calls if '--force-recreate' in c]
                self.assertEqual(refresh[-1], 'retrieval')
                self.assertIn('--no-deps', refresh)
                self.assertLess(calls.index(['dependency', 'provision-client-key.sh']), calls.index(refresh))
                self.assertLess(calls.index(refresh), calls.index(INDEX))
                self.assertLess(calls.index(refresh), calls.index(['moyai', 'up']))
                self.assertLess(calls.index(INDEX), calls.index(SMOKE))

    def test_restart_uses_the_same_ordered_shutdown_and_complete_startup(self):
        result = self.run_make('restart')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn(['moyai', 'down'], calls)
        self.assertLess(calls.index(['moyai', 'down']), calls.index(['moyai', 'up']))
        self.assertLess(calls.index(['docker', 'model', 'unload', 'fixture-coder', 'fixture-embed']),
                        calls.index(['moyai', 'up']))

    def test_down_stops_all_clients_before_unloading_only_owned_models_and_keeps_data(self):
        result = self.run_make('down')
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.state_file.read_text())
        self.assertFalse(any(state[k] for k in ('core', 'workspace', 'connected', 'moyai')))
        self.assertEqual(state['loaded'], ['unrelated-model'])
        self.assertEqual(state['volumes'], self.initial['volumes'])
        self.assertEqual(self.calls()[0], ['moyai', 'down'])
        self.assertEqual(self.run_make('down').returncode, 0)
        self.assertEqual(json.loads(self.state_file.read_text()), state)

    def test_down_without_env_still_stops_the_stack_using_repository_model_defaults(self):
        (self.root / '.env').unlink()
        self.state_file.write_text(json.dumps(self.initial | {
            'loaded': ['template-coder', 'template-embed', 'unrelated-model']}))
        result = self.run_make('down')
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.state_file.read_text())
        self.assertFalse(state['moyai'] or state['core'])
        self.assertEqual(state['loaded'], ['unrelated-model'])

    def test_ambient_compose_overrides_do_not_redirect_the_platform_lifecycle(self):
        overrides = {'COMPOSE_PROJECT_NAME': 'unrelated-project', 'COMPOSE_FILE': '/tmp/unrelated.yaml',
                     'COMPOSE_PROFILES': 'connected'}
        result = self.run_make('down', **overrides)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.state_file.read_text())
        self.assertTrue(state['unrelated_project'])
        self.assertFalse(state['core'] or state['moyai'])
        result = self.run_make('up', **overrides)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.state_file.read_text())
        self.assertTrue(state['core'] and state['moyai'] and state['unrelated_project'])

    def test_gateway_and_shutdown_use_the_same_env_models_despite_shell_overrides(self):
        overrides = {'QWEN_MODEL': 'shell-coder', 'EMBEDDING_MODEL': 'shell-embed'}
        result = self.run_make('up', **overrides)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.state_file.read_text())['gateway_models'],
                         ['fixture-coder', 'fixture-embed'])
        result = self.run_make('down', **overrides)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.state_file.read_text())['loaded'], ['unrelated-model'])

    def test_down_attempts_every_phase_but_reports_any_cleanup_failure(self):
        for failure in ('FAIL_MOYAI', 'FAIL_CORE', 'FAIL_UNLOAD'):
            with self.subTest(failure=failure):
                self.events.write_text('')
                self.state_file.write_text(json.dumps(self.initial))
                result = self.run_make('down', **{failure: '1'})
                self.assertNotEqual(result.returncode, 0)
                calls = self.calls()
                self.assertIn(['moyai', 'down'], calls)
                self.assertTrue(any(call[0:2] == ['docker', 'compose'] and 'down' in call for call in calls))
                self.assertIn(['docker', 'model', 'unload', 'fixture-coder', 'fixture-embed'], calls)
