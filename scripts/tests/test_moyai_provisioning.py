"""Private bootstrap recovery and actual controller reload decisions."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class MoyaiProvisioningTests(unittest.TestCase):
    def preparer(self):
        spec = importlib.util.spec_from_file_location('moyai_preparer', ROOT / 'scripts/moyai-prepare.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_replacement_keys_have_unique_aliases_for_litellm_rotation(self):
        module = self.preparer()
        self.assertTrue(hasattr(module, 'new_key_payload'), 'Unique replacement key aliases are missing')
        first, second = module.new_key_payload(), module.new_key_payload()
        self.assertNotEqual(first['key_alias'], second['key_alias'])
        for payload in (first, second):
            self.assertRegex(payload['key_alias'], r'^moyai-local-mvp-[0-9a-f]{32}$')
            self.assertEqual(payload['models'], ['local-qwen'])
            self.assertEqual(payload['allowed_routes'], ['/v1/responses','/model/info'])
            self.assertEqual(payload['object_permission'], {'mcp_servers': [], 'vector_stores': []})

    def test_owned_legacy_inference_key_is_narrowed_in_place_and_verified(self):
        module = self.preparer()
        self.assertTrue(hasattr(module,'prepare_key'),'Inference route migration is missing')
        state = {'key_alias':'moyai-local-mvp', 'models':['local-qwen'],
                 'allowed_routes':['openai_routes','/model/info'], 'status':'active',
                 'object_permission':{'mcp_servers':[],'vector_stores':[]}}
        updates=[]
        def gateway(master,route,body=None,**_):
            if route.startswith('/key/info?'): return {'info':dict(state)}
            self.assertEqual(route,'/key/update')
            self.assertEqual(body['key'],'fixture-owned')
            updates.append(body); state.update({k:v for k,v in body.items() if k!='key'})
            return {}
        module.request=gateway
        self.assertEqual(module.prepare_key('fixture-master','fixture-owned'),'fixture-owned')
        self.assertEqual(len(updates),1)
        self.assertEqual(state['allowed_routes'],['/v1/responses','/model/info'])

    def test_missing_password_is_recovered_without_changing_existing_account(self):
        module = self.preparer()
        self.assertTrue(hasattr(module, 'bootstrap_password'), 'Bootstrap recovery is missing')
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            (local / 'runtime-config.json').write_text(json.dumps({'password': 'fixture-retained-password'}))
            self.assertEqual(module.bootstrap_password(local, retained_data=True), 'fixture-retained-password')
            self.assertEqual((local / 'admin-password').read_text().strip(), 'fixture-retained-password')
            self.assertEqual((local / 'admin-password').stat().st_mode & 0o777, 0o600)

    def test_lost_bootstrap_credentials_with_retained_data_fail_with_recovery_guidance(self):
        module = self.preparer()
        self.assertTrue(hasattr(module, 'bootstrap_password'), 'Bootstrap recovery is missing')
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            with self.assertRaisesRegex(Exception, 'restore'):
                module.bootstrap_password(local, retained_data=True)
            self.assertFalse((local / 'admin-password').exists())

    def test_ui_connection_sets_only_the_local_url_and_is_idempotent(self):
        module = self.preparer()
        self.assertTrue(hasattr(module, 'connect_ui'), 'LiteLLM UI connection is missing')
        state = {'moyai_url': None, 'enable_chat_ui': False, 'disable_custom_api_keys': True}
        patches = []

        def gateway(key, route, body=None, *, method=None):
            self.assertEqual(key, 'fixture-admin-key')
            if route == '/get/ui_settings':
                self.assertIsNone(body)
                return {'values': dict(state)}
            self.assertEqual((route, method), ('/update/ui_settings', 'PATCH'))
            patches.append(body)
            state.update(body)
            return {'status': 'success'}

        module.request = gateway
        module.connect_ui('fixture-admin-key')
        module.connect_ui('fixture-admin-key')
        self.assertEqual(state, {'moyai_url': 'http://127.0.0.1:8787',
                                'enable_chat_ui': False, 'disable_custom_api_keys': True})
        self.assertEqual(patches, [{'moyai_url': 'http://127.0.0.1:8787'}])

    def test_ui_connection_rejects_a_gateway_that_did_not_apply_the_setting(self):
        module = self.preparer()
        self.assertTrue(hasattr(module, 'connect_ui'), 'LiteLLM UI connection is missing')
        module.request = lambda *_args, **_kwargs: {'values': {'moyai_url': None}}
        with self.assertRaises(module.ProvisionError):
            module.connect_ui('fixture-admin-key')

    def test_startup_reloads_changed_controller_and_requires_healthy_context(self):
        for changed, existing, healthy in [('changed', True, True), ('unchanged', True, True),
                                           ('changed', False, True), ('unchanged', True, False)]:
            with self.subTest(changed=changed, existing=existing, healthy=healthy), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'scripts').mkdir()
                for script in ('lib.sh', 'moyai.sh'):
                    shutil.copy(ROOT / 'scripts' / script, root / 'scripts' / script)
                binaries = root / 'bin'
                binaries.mkdir()
                (binaries / 'python3').write_text('''#!/usr/bin/env python3_fixture
import json, os, sys
with open(os.environ['COMMAND_LOG'], 'a') as stream:
    stream.write(json.dumps(['python3', *sys.argv[1:]]) + '\\n')
if sys.argv[1:] == ['scripts/moyai-prepare.py']:
    print(os.environ['CONFIG_CHANGE'])
elif sys.argv[1:] != ['scripts/moyai-prepare.py', '--ui', 'connect']:
    raise SystemExit(1)
'''.replace('/usr/bin/env python3_fixture', str(Path(os.sys.executable))))
                (binaries / 'ssh-keygen').write_text('#!/bin/sh\nexit 0\n')
                (binaries / 'docker').write_text('''#!/usr/bin/env python3_fixture
import json, os, sys
with open(os.environ['COMMAND_LOG'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[-3:] == ['ps','-q','moyai'] and os.environ['EXISTING'] == 'yes':
    print('fixture-controller')
if sys.argv[-1] == 'repository-context' and os.environ['CONTEXT_HEALTHY'] == 'no':
    raise SystemExit(7)
'''.replace('/usr/bin/env python3_fixture', str(Path(os.sys.executable))))
                for binary in binaries.iterdir():
                    binary.chmod(0o755)
                log = root / 'commands.jsonl'
                result = subprocess.run(['bash', 'scripts/moyai.sh', 'up'], cwd=root,
                    env=os.environ | {'PATH': str(binaries) + os.pathsep + os.environ['PATH'],
                        'CONFIG_CHANGE': changed, 'EXISTING': 'yes' if existing else 'no',
                        'CONTEXT_HEALTHY': 'yes' if healthy else 'no', 'COMMAND_LOG': str(log)},
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 0 if healthy else 7, result.stderr)
                self.assertEqual('Local Moyai is ready' in result.stdout, healthy)
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual(calls[-2], ['python3', 'scripts/moyai-prepare.py', '--ui', 'connect'])
                context = calls[-1]
                self.assertEqual(context[-1], 'repository-context')
                for flag in ('up', '--wait', '--wait-timeout', '--no-deps', '--no-recreate'):
                    self.assertIn(flag, context)
                for call in calls:
                    if call[0] == 'compose':
                        self.assertIn('--project-name', call)
                        self.assertEqual(call[call.index('--project-name') + 1], 'local-ai-moyai')
                forced = [call for call in calls if '--force-recreate' in call]
                self.assertEqual(bool(forced), changed == 'changed' and existing)
                if forced:
                    self.assertEqual(forced[0][-1], 'moyai')
                    self.assertIn('--no-deps', forced[0])
