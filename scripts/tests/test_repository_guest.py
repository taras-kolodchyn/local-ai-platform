"""Pure Python tests for the fixed guest verification boundary."""
import base64
import hashlib
import importlib.util
import io
from unittest import mock
import hmac
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
GUEST = ROOT / 'integrations/moyai/repository_guest.py'
CONVERSATION = 'a' * 64
RETRY_CANDIDATE_BASE = """\
const OWNER_FILTER: &str = "owner = ?";

pub fn legacy_retry_delay_ms(attempt: u32) -> u64 {
    bounded_backoff_ms(attempt)
}
"""


class GuestTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(GUEST.exists(), 'bounded guest runner is missing')
        spec = importlib.util.spec_from_file_location('guest', GUEST)
        self.guest = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.guest)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.temp_path = Path(self.temp.name).resolve()
        self.runner = self.guest.Guest(self.temp_path, ROOT / 'integrations/moyai/oracles')

    def request(self, op, **kwargs):
        return self.runner.handle({'op': op, 'conversation_id': CONVERSATION, **kwargs})

    def payload(self):
        files = []
        manifest = []
        for path in sorted((ROOT / 'examples/rust-service').rglob('*')):
            if path.is_file() and 'target' not in path.parts:
                name = path.relative_to(ROOT / 'examples/rust-service').as_posix()
                data = path.read_bytes()
                files.append({'path': name, 'content_base64': base64.b64encode(data).decode()})
                manifest.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
        metadata = {'schema_version': 1, 'repository_id': 'rust', 'verification_profile': 'rust-retry', 'files': manifest}
        metadata['snapshot_id'] = self.guest.digest(metadata)
        return {'manifest': metadata, 'files': files}

    def imported(self):
        self.request('import', **self.payload())
        return self.temp_path / CONVERSATION / 'repository'

    def test_malformed_oversized_duplicate_and_unknown_input(self):
        for data in ('[]', '{', '{"op":"tree","op":"check"}', 'x'*(self.guest.MAX_INPUT+1)):
            with self.subTest(size=len(data)):
                with self.assertRaises(ValueError):
                    self.guest.read_request(io.StringIO(data))
        with self.assertRaises(ValueError):
            self.request('tree', command='rm -rf /')
        with self.assertRaises(ValueError):
            self.runner.handle({'op': 'tree', 'conversation_id': '../escape'})

    def test_malformed_operation_and_file_types_reject_cleanly(self):
        with self.assertRaises(ValueError):
            self.runner.handle({'op': [], 'conversation_id': CONVERSATION})
        payload = self.payload()
        payload['files'][0]['path'] = []
        with self.assertRaises(ValueError):
            self.request('import', **payload)

    def test_import_hash_mismatch_and_paths_rejected_without_partial_project(self):
        payload = self.payload()
        payload['files'][0]['content_base64'] = base64.b64encode(b'changed').decode()
        with self.assertRaises(ValueError):
            self.request('import', **payload)
        self.assertFalse((self.temp_path/CONVERSATION/'repository').exists())
        for path in ('../outside', '/etc/passwd', '.cargo/config.toml', 'build.rs', 'tests/../Cargo.toml'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.guest.safe_relative(path)

    def test_tree_protects_package_and_symlink_paths(self):
        project = self.imported()
        tree = self.request('tree')
        self.assertTrue(tree['protected_unchanged'])
        self.assertEqual(len(tree['tree_hash']), 64)
        (project/'Cargo.toml').write_text('bad')
        self.assertFalse(self.request('tree')['protected_unchanged'])
        (project/'Cargo.toml').unlink()
        (project/'Cargo.toml').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            self.request('tree')

    def test_tree_rejects_hardlinked_source_files(self):
        project = self.imported()
        (project/'src/lib.rs').unlink()
        os.link(project/'src/main.rs', project/'src/lib.rs')
        with self.assertRaises(ValueError):
            self.request('tree')

    def test_existing_import_does_not_overwrite_edits(self):
        project = self.imported()
        (project/'src/lib.rs').write_text('edited')
        with self.assertRaises(ValueError):
            self.request('import', **self.payload())
        self.assertEqual((project/'src/lib.rs').read_text(), 'edited')

    def test_receipt_counts_only_executed_changed_regression_targets(self):
        self.assertTrue(hasattr(self.guest, 'test_count'), 'native test output counter missing')
        self.assertEqual(self.guest.test_count('test result: ok. 2 passed; 0 failed; 0 ignored;'), 2)
        self.assertEqual(self.guest.test_count('test result: ok. 0 passed; 0 failed; 2 ignored;'), 0)
        self.assertEqual(self.guest.test_count('2 passed'), 0)

    def test_intermediate_command_never_settles_check(self):
        state = self.temp_path/'operation.json'
        state.write_text(json.dumps({'state': 'running'}))
        result = self.runner.run_process([sys.executable, '-c', 'print("ok")'], self.temp_path,
                                         dict(os.environ), 1, state, settle=False)
        self.assertEqual(result['state'], 'passed')
        self.assertEqual(result['descendants_stopped'], sys.platform == 'linux')
        self.assertEqual(json.loads(state.read_text())['state'], 'running')

    def test_cancel_before_process_start_is_recorded(self):
        self.imported()
        state = self.runner.operation_path(CONVERSATION, 'prestart')
        state.write_text(json.dumps({'state': 'running', 'owner_pid': 999999999}))
        result = self.request('cancel', operation_id='prestart')
        self.assertFalse(state.with_suffix('.cancel').exists())
        self.assertEqual(result['state'], 'unknown')

    def test_nested_json_rejected_with_valueerror(self):
        with self.assertRaises(ValueError):
            self.guest.read_request(io.StringIO('['*2000 + ']'*2000))

    def test_added_test_identity_requires_new_function_not_edited_old_test(self):
        self.assertTrue(hasattr(self.guest, 'test_names'), 'regression identity parser missing')
        names = self.guest.test_names('#[test]\nfn original() {}\n#[test]\nfn retry_ceiling() {}')
        self.assertEqual(names, {'original', 'retry_ceiling'})
        self.assertEqual(self.guest.passed_test_names('test retry_ceiling ... ok\ntest ignored ... ignored'), {'retry_ceiling'})

    def test_receipt_mac_authenticates_outcome_without_persisting_key(self):
        self.assertTrue(hasattr(self.guest, 'sign_receipt'), 'authenticated receipt missing')
        key = 'c'*64
        receipt = {'state': 'passed', 'checks': {'oracle_passed': True}}
        signed = self.guest.sign_receipt(receipt, key)
        body = json.dumps(receipt, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
        self.assertEqual(signed['receipt_mac'], hmac.new(bytes.fromhex(key), body, hashlib.sha256).hexdigest())
        self.assertNotIn(key, json.dumps(signed))
        self.assertNotEqual(self.guest.sign_receipt(receipt | {'state': 'failed'}, key)['receipt_mac'], signed['receipt_mac'])
        self.assertEqual(self.guest.sign_receipt(receipt, None), receipt)

    def test_cached_receipt_is_never_resigned_with_new_key(self):
        self.imported()
        path = self.runner.operation_path(CONVERSATION, 'cached')
        original = {'state': 'passed', 'ok': True, 'receipt_mac': 'old-mac'}
        path.write_text(json.dumps(original))
        reply = self.request('check', operation_id='cached', task_id='task', profile='rust-retry', timeout_seconds=1, receipt_key='d'*64)
        self.assertEqual(reply, original)

    def test_verification_copy_mutation_or_new_build_config_is_detected(self):
        self.assertTrue(hasattr(self.guest, 'matches_files'), 'verification copy guard missing')
        copied = self.temp_path/'copy'
        copied.mkdir()
        (copied/'lib.rs').write_bytes(b'original')
        expected = {'lib.rs': hashlib.sha256(b'original').hexdigest()}
        self.assertTrue(self.guest.matches_files(copied, expected))
        (copied/'lib.rs').write_bytes(b'replaced')
        self.assertFalse(self.guest.matches_files(copied, expected))
        (copied/'lib.rs').write_bytes(b'original')
        (copied/'.cargo').mkdir()
        (copied/'.cargo/config.toml').write_text('new config')
        self.assertFalse(self.guest.matches_files(copied, expected))

    def test_response_wire_is_bounded_even_with_json_escaping(self):
        self.assertTrue(hasattr(self.guest, 'encode_response'), 'response encoding bound missing')
        self.guest.MAX_INPUT = 128
        wire = self.guest.encode_response({'ok': True, 'text': '\x01'*100})
        self.assertLessEqual(len(wire.encode()), 128)
        self.assertFalse(json.loads(wire)['ok'])

    def test_fixed_argv_and_environment(self):
        project = self.imported()
        argv, env = self.runner.command(project, self.temp_path/'target')
        self.assertEqual(argv[:2], ['/opt/rust/bin/cargo', 'test'])
        for flag in ('--offline', '--locked', '--jobs', '1'):
            self.assertIn(flag, argv)
        self.assertEqual(env['CARGO_NET_OFFLINE'], 'true')
        self.assertNotIn('RUSTC_WRAPPER', env)
        self.assertNotIn('HTTP_PROXY', env)

    def test_output_bound_and_timeout_kill_process_group(self):
        state = self.temp_path/'operation.json'
        state.write_text(json.dumps({'state': 'running'}))
        receipt = self.runner.run_process([sys.executable, '-c',
            'import os,time; print("x"*200000,flush=True); os.fork(); time.sleep(20)'],
            self.temp_path, dict(os.environ), 0.2, state)
        self.assertEqual(receipt['state'], 'timeout')
        self.assertLessEqual(len(receipt['output'].encode()), self.guest.MAX_OUTPUT)
        self.assertTrue(receipt['output_truncated'])
        with self.assertRaises(ProcessLookupError):
            os.killpg(receipt['process_group'], 0)

    def test_invalid_utf8_output_stays_within_encoded_output_budget(self):
        state = self.temp_path/'binary.json'
        state.write_text(json.dumps({'state': 'running'}))
        receipt = self.runner.run_process([sys.executable, '-c', 'import os; os.write(1, bytes([255])*70000)'],
                                          self.temp_path, dict(os.environ), 1, state)
        self.assertLessEqual(len(receipt['output'].encode()), self.guest.MAX_OUTPUT)
        self.assertTrue(receipt['output_truncated'])

    def test_closed_output_does_not_disable_process_timeout(self):
        state = self.temp_path/'closed.json'
        state.write_text(json.dumps({'state': 'running'}))
        receipt = self.runner.run_process([sys.executable, '-c',
            'import os,time; os.close(1); os.close(2); time.sleep(.5)'], self.temp_path, dict(os.environ), .1, state)
        self.assertEqual(receipt['state'], 'timeout')
        self.assertLess(receipt['elapsed_seconds'], .4)

    def run_diagnostic_stream(self, data, cargo=True):
        source = self.temp_path/'child-output.bin'
        source.write_bytes(data)
        state = self.temp_path/'diagnostics.json'
        state.write_text(json.dumps({'state': 'running'}))
        argv = [sys.executable, '-c',
                'import sys; from pathlib import Path; sys.stdout.buffer.write(Path(sys.argv[1]).read_bytes()); sys.exit(101)',
                str(source)] + (['--message-format=json'] if cargo else [])
        return self.runner.run_process(argv, self.temp_path, dict(os.environ), 3, state)

    def test_late_cargo_error_survives_raw_prefix_limit(self):
        artifact = {'reason': 'compiler-artifact', 'executable': None, 'target': {'name': 'dependency'}, 'profile': {'test': False}}
        diagnostic = {'reason': 'compiler-message', 'message': {'level': 'error', 'code': {'code': 'E0308'},
            'message': 'mismatched types', 'rendered': 'error[E0308]: mismatched types\n --> tests/retry.rs:7:9\n expected u64, found u32\n'}}
        data = (json.dumps(artifact)+'\n').encode()*1000 + (json.dumps(diagnostic)+'\n').encode()
        result = self.run_diagnostic_stream(data)
        self.assertEqual(result['exit_code'], 101)
        self.assertEqual(result['state'], 'failed')
        self.assertTrue(result['process_group_stopped'] and result['pipe_drained'])
        self.assertEqual(len(result['output'].encode()), self.guest.MAX_OUTPUT)
        self.assertNotIn('E0308', result['output'])
        self.assertTrue(result['output_truncated'])
        self.assertEqual(result['diagnostics'][0]['code'], 'E0308')
        self.assertIn('tests/retry.rs:7:9', result['diagnostics'][0]['rendered'])
        self.assertFalse(result['diagnostics_truncated'])

    def test_cargo_diagnostics_skip_bad_and_whole_oversized_lines(self):
        def record(message):
            return json.dumps({'reason': 'compiler-message', 'message': {'level': 'error', 'message': message}}).encode()
        data = b'{bad\n{"message":"\xff"}\n' + b'['*3000 + b']'*3000 + b'\n'
        data += b'x'*(self.guest.MAX_FILE+8192) + record('forged suffix') + b'\n'
        data += record('real error after oversized line')  # Valid final line without newline.
        result = self.run_diagnostic_stream(data)
        self.assertEqual([d['message'] for d in result['diagnostics']], ['real error after oversized line'])
        self.assertTrue(result['diagnostics_truncated'])
        self.assertEqual(result['exit_code'], 101)
        self.assertTrue(result['pipe_drained'])

    def test_diagnostic_unicode_count_and_byte_budgets_and_truncated_json(self):
        diagnostic = {'reason': 'compiler-message', 'message': {'level': 'error', 'code': {'code': 'E1234'},
            'message': '\U0001f6a8'*1000, 'rendered': '\u043f'*10000 + '\ud800'}}
        result = self.run_diagnostic_stream((json.dumps(diagnostic)+'\n').encode()*100 + b'{"reason":"compiler-message"')
        self.assertTrue(result['diagnostics_truncated'])
        self.assertLessEqual(len(result['diagnostics']), self.guest.MAX_DIAGNOSTICS)
        self.assertGreater(len(result['diagnostics']), 0)
        self.assertTrue(all(d['truncated'] for d in result['diagnostics']))
        text_bytes = sum(len(v.encode()) for d in result['diagnostics'] for v in d.values() if isinstance(v,str))
        self.assertLessEqual(text_bytes, self.guest.MAX_DIAGNOSTIC_BYTES)
        self.assertLessEqual(len(json.dumps(result['diagnostics']).encode()), 6*self.guest.MAX_DIAGNOSTIC_BYTES+2048)
        self.assertLessEqual(len(result['output_tail'].encode()), self.guest.MAX_TAIL)
        self.assertLessEqual(len(self.guest.encode_response(result).encode()), self.guest.MAX_INPUT)
        self.assertEqual(json.loads(self.guest.encode_response(result))['diagnostics'], result['diagnostics'])
        small = {'reason': 'compiler-message', 'message': {'level': 'error', 'message': 'short error'}}
        crowded = self.run_diagnostic_stream((json.dumps(small)+'\n').encode()*100)
        self.assertEqual(len(crowded['diagnostics']), self.guest.MAX_DIAGNOSTICS)
        self.assertTrue(crowded['diagnostics_truncated'])

    def test_non_json_failure_tail_preserves_late_cause_and_utf8_bound(self):
        result = self.run_diagnostic_stream(b'x'*70000+b'\xff'*5000+b'\nfatal: linker unavailable\n', cargo=False)
        self.assertEqual(result['diagnostics'], [])
        self.assertIn('fatal: linker unavailable', result['output_tail'])
        self.assertNotIn('fatal:', result['output'])
        self.assertTrue(result['output_truncated'] and result['output_tail_truncated'])
        self.assertLessEqual(len(result['output_tail'].encode()), self.guest.MAX_TAIL)
        self.assertEqual(result['state'], 'failed')

    def test_cancel_waits_until_process_group_stopped(self):
        self.imported()
        state = self.runner.operation_path(CONVERSATION, 'cancel-test')
        state.write_text(json.dumps({'state': 'running', 'operation_id': 'cancel-test', 'owner_identity':self.guest.incarnation(os.getpid())}))
        result = {}
        def run():
            result.update(self.runner.run_process([sys.executable, '-c', 'import time; time.sleep(20)'],
                          self.temp_path, dict(os.environ), 10, state))
        thread = threading.Thread(target=run)
        thread.start()
        for _ in range(100):
            if json.loads(state.read_text()).get('process_group'):
                break
            time.sleep(.01)
        cancelled = self.request('cancel', operation_id='cancel-test')
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual(cancelled['state'], 'cancelled')

    def test_missing_status_is_side_effect_free_and_cancel_tombstone_fences_replay(self):
        first = self.request('status', operation_id='lost')
        self.assertEqual(first['state'], 'unknown')
        path = self.runner.operation_path(CONVERSATION, 'lost', create=False)
        self.assertFalse(path.exists())
        self.request('cancel', operation_id='lost')
        self.assertTrue(path.exists())
        receipt = self.request('check', operation_id='lost', task_id='task', profile='rust-retry', timeout_seconds=1)
        self.assertEqual(receipt['state'], 'unknown')

    def test_exact_native_completion_rejects_duplicates_skips_and_extra_names(self):
        self.assertTrue(hasattr(self.guest, 'completed_tests'), 'exact native completions missing')
        good = 'running 1 test\ntest module::retry ... ok\n\ntest result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\n'
        self.assertTrue(self.guest.completed_tests(good, ['module::retry'], True))
        for output in (good.replace(' ... ok', ' ... ignored'), good+good, good.replace('module::retry', 'other::retry'), good.replace('1 passed', '0 passed'), good+'test unexpected ... mystery\n'):
            self.assertFalse(self.guest.completed_tests(output, ['module::retry'], True))

    def test_regression_names_preserve_module_identity(self):
        source='mod first { #[test] fn retry() {} } mod second { #[test] fn retry() {} }'
        self.assertEqual(self.guest.test_names(source), {'first::retry','second::retry'})

    def test_cargo_artifacts_include_only_compiled_test_harnesses(self):
        state=self.temp_path/'artifacts.json'
        state.write_text(json.dumps({'state':'running'}))
        records=[{'reason':'compiler-artifact','executable':'/normal/server','target':{'name':'server'},'profile':{'test':False}},
                 {'reason':'compiler-artifact','executable':'/test/server','target':{'name':'server'},'profile':{'test':True}}]
        code='import json; records='+repr(records)+'; [print(json.dumps(record)) for record in records]'
        reply=self.runner.run_process([sys.executable,'-c',code,'--message-format=json'],self.temp_path,dict(os.environ),1,state)
        self.assertEqual([item['executable'] for item in reply['artifacts']], ['/test/server'])

    def test_counterexample_changes_retry_only_and_preserves_other_repairs(self):
        candidate = RETRY_CANDIDATE_BASE + """
pub fn bounded_backoff_ms(attempt: u32) -> u64 { attempt.min(300) as u64 }
"""
        template = (ROOT / 'integrations/moyai/oracles/counterexample_lib.rs').read_text()
        broken = self.guest.counterexample_source(candidate, template)
        self.assertIn('const OWNER_FILTER: &str = "owner = ?";', broken)
        self.assertNotIn('attempt.min(300)', broken)
        self.assertNotIn('bounded_backoff_ms(attempt)', broken)
        self.assertIn('60_000', broken)

    def test_const_helper_keeps_qualifier_in_retry_counterexample(self):
        candidate = RETRY_CANDIDATE_BASE + """
pub const fn bounded_backoff_ms(attempt: u32) -> u64 {
    if attempt >= 9 { 30_000 } else { 100_u64 << attempt }
}
"""
        template = (ROOT / 'integrations/moyai/oracles/counterexample_lib.rs').read_text()
        broken = self.guest.counterexample_source(candidate, template)
        self.assertIn('pub const fn bounded_backoff_ms', broken)
        self.assertNotIn('30_000', broken)
        self.assertIn('60_000', broken)

    def test_active_or_owner_lost_same_run_scratch_is_not_deleted(self):
        self.imported()
        directory=self.temp_path/CONVERSATION
        for index,owner in enumerate((self.guest.incarnation(os.getpid()), {'pid':999999999})):
            scratch=directory/'.repository-state/builds'/f'active_{index}'
            scratch.mkdir(parents=True)
            operation=self.runner.operation_path(CONVERSATION,f'active_{index}')
            operation.write_text(json.dumps({'state':'running','processes_stopped':True,'owner_identity':owner}))
            with self.assertRaises(ValueError): self.runner.recover_scratch(directory)
            self.assertTrue(scratch.exists())
            import shutil
            shutil.rmtree(scratch)

    def test_missing_run_identity_is_not_verified_sandbox_turnover(self):
        self.imported()
        directory=self.temp_path/CONVERSATION
        scratch=directory/'.repository-state/builds/missing-run'
        scratch.mkdir(parents=True)
        operation=self.runner.operation_path(CONVERSATION,'missing-run')
        operation.write_text(json.dumps({'state':'running','processes_stopped':True,'owner_identity':{'pid':999999999}}))
        current={'pid':os.getpid(),'boot_id':'166ce004-97fd-4a48-ae4b-01baedad777b','pid_namespace':123,'init_start_ticks':100}
        with mock.patch.object(self.guest,'incarnation',return_value=current):
            with self.assertRaises(ValueError): self.runner.recover_scratch(directory)
        self.assertTrue(scratch.exists())

    def test_live_owner_cancel_records_intent_during_reaped_child_handoff(self):
        self.imported()
        path=self.runner.operation_path(CONVERSATION,'handoff')
        path.write_text(json.dumps({'state':'running','operation_id':'handoff','owner_identity':self.guest.incarnation(os.getpid()),'process_identity':{'pid':999999999}}))
        with mock.patch.object(self.guest.os,'killpg') as kill:
            reply=self.request('cancel',operation_id='handoff')
        self.assertTrue(path.with_suffix('.cancel').exists())
        kill.assert_not_called()
        self.assertIn(reply['state'],{'running','unknown','cancelled'})

    def test_failed_spawn_is_not_an_executed_command(self):
        self.imported()
        import shutil
        oracles=self.temp_path/'oracles'
        shutil.copytree(ROOT/'integrations/moyai/oracles',oracles)
        for name in ('Verifier.lock','Baseline.lock'): (oracles/name).write_text('test-only lock; command will not launch')
        self.runner.oracles=oracles
        self.guest.TRUSTED_CWD=self.temp_path
        with mock.patch.object(self.runner,'command',return_value=(['/definitely/not/a/tool','--all-targets'],dict(os.environ))):
            reply=self.request('check',operation_id='launch-error',task_id='task',profile='rust-retry',timeout_seconds=1,receipt_key='e'*64)
        self.assertEqual(reply['state'],'failed')
        self.assertTrue(reply['no_process_started'])
        self.assertTrue(reply['processes_stopped'])
        self.assertEqual(reply['commands'],[])
        self.assertEqual(len(reply['launch_errors']),1)
        self.assertIn('receipt_mac',reply)

    def test_spawn_error_after_prior_command_preserves_executed_evidence(self):
        self.imported()
        import shutil
        oracles=self.temp_path/'later-oracles'
        shutil.copytree(ROOT/'integrations/moyai/oracles',oracles)
        for name in ('Verifier.lock','Baseline.lock'): (oracles/name).write_text('test-only lock')
        self.runner.oracles=oracles; self.guest.TRUSTED_CWD=self.temp_path
        calls=[]
        def command(project,target):
            calls.append(project)
            argv=[sys.executable,'-c','print("first executed command")','--all-targets'] if len(calls)==1 else ['/missing/later/tool','--all-targets']
            return argv,dict(os.environ)
        with mock.patch.object(self.runner,'command',side_effect=command):
            reply=self.request('check',operation_id='later-launch-error',task_id='task',profile='rust-retry',timeout_seconds=2,receipt_key='e'*64)
        if sys.platform != 'linux':
            self.assertEqual(reply['state'], 'unknown')
            self.assertFalse(reply['processes_stopped'])
            self.assertEqual(len(calls), 1)
            return
        self.assertEqual(reply['state'],'failed')
        self.assertFalse(reply['no_process_started'])
        self.assertTrue(reply['processes_stopped'])
        self.assertEqual(len(reply['commands']),1)
        self.assertEqual(reply['commands'][0]['exit_code'],0)
        self.assertEqual(len(reply['launch_errors']),1)
        self.assertIn('receipt_mac',reply)

    def test_cancel_does_not_signal_old_child_after_lock_handoff(self):
        self.imported()
        path=self.runner.operation_path(CONVERSATION,'lock-handoff')
        child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'],start_new_session=True)
        self.addCleanup(child.wait)
        self.addCleanup(lambda: child.kill() if child.poll() is None else None)
        record={'state':'running','operation_id':'lock-handoff','owner_identity':self.guest.incarnation(os.getpid()),'process_identity':self.guest.incarnation(child.pid)}
        path.write_text(json.dumps(record))
        from contextlib import contextmanager
        original=self.guest.operation_lock
        @contextmanager
        def changed_while_waiting(destination):
            with original(destination):
                record['process_identity']={'pid':999999999}
                self.guest.save_json(path,record)
                yield
                self.guest.save_json(path,record | {'state':'cancelled'})
        with mock.patch.object(self.guest,'operation_lock',changed_while_waiting), mock.patch.object(self.guest.os,'killpg') as kill:
            reply=self.request('cancel',operation_id='lock-handoff')
        self.assertEqual(reply['state'],'cancelled')
        self.assertTrue(path.with_suffix('.cancel').exists())
        kill.assert_not_called()
        child.kill(); child.wait()

    def test_actual_spawn_failures_settle_in_controller_and_allow_repair(self):
        sys.path.insert(0,str(ROOT/'integrations/moyai'))
        from repository_ledger import Ledger
        import shutil
        self.imported()
        oracles=self.temp_path/'consumer-oracles'
        shutil.copytree(ROOT/'integrations/moyai/oracles',oracles)
        for name in ('Verifier.lock','Baseline.lock'): (oracles/name).write_text('test-only lock')
        self.runner.oracles=oracles; self.guest.TRUSTED_CWD=self.temp_path
        tree=self.request('tree')['tree_hash']
        for prior in (False,True):
            with self.subTest(prior=prior):
                ledger=Ledger(self.temp_path/('consumer-'+str(prior)+'.db'),clock=lambda:100)
                ledger.attach('session','authority',{'repository_id':'repo','snapshot_id':'snapshot','generation_id':'generation','store_id':'store','verification_profile':'rust-retry'})
                ledger.imported('session','authority',tree)
                ledger.start_check('session','authority','baseline',tree)
                task=ledger.status('session','authority')['task_id']
                baseline={'ok':False,'state':'failed','task_id':task,'operation_id':'baseline','tree_hash':tree,'profile':'rust-retry','processes_stopped':True,
                          'commands':[{'kind':'baseline_probe','argv':['/operator/probe'],'exit_code':101,'process_group_stopped':True,'descendants_stopped':True,'pipe_drained':True,
                                       'output':'legacy_large_attempt_obeys_documented_ceiling ... FAILED\nleft: 60000\nright: 30000'}],
                          'checks':{'protected_unchanged':True,'tree_unchanged':True,'verification_inputs_unchanged':True,'baseline_policy_violation':True}}
                baseline=self.guest.sign_receipt(baseline,ledger.check_key('session','authority','baseline'))
                self.assertEqual(ledger.finish_check('session','authority','baseline',baseline,tree),'analyzing')
                ledger.cite('session','authority',[{'path':'src/lib.rs','start_line':1,'end_line':5,'chunk_id':'policy'}])
                ledger.begin('session','authority',[{'content':'Acceptance criteria: cap retry backoff at 30000 ms and run regression tests.'}])
                operation='consumer-later' if prior else 'consumer-first'
                ledger.start_check('session','authority',operation,tree)
                calls=[]
                def command(project,target):
                    calls.append(project)
                    argv=[sys.executable,'-c','print("executed")','--all-targets'] if prior and len(calls)==1 else ['/missing/controller-tool','--all-targets']
                    return argv,dict(os.environ)
                with mock.patch.object(self.runner,'command',side_effect=command):
                    receipt=self.request('check',operation_id=operation,task_id=task,profile='rust-retry',timeout_seconds=2,
                                         receipt_key=ledger.check_key('session','authority',operation))
                if prior and sys.platform != 'linux':
                    self.assertEqual(receipt['state'], 'unknown')
                    self.assertFalse(receipt['processes_stopped'])
                    self.assertEqual(len(calls), 1)
                    continue
                self.assertEqual(receipt['state'],'failed')
                self.assertEqual(len(receipt['commands']),int(prior))
                self.assertEqual(len(receipt['launch_errors']),1)
                self.assertEqual(ledger.finish_check('session','authority',operation,receipt,tree),'implementing')
                self.assertEqual(ledger.status('session','authority')['pending'],None)
                self.assertTrue(ledger.start_check('session','authority','repair-next',tree)['dispatch'])

    def test_mandatory_baseline_failure_prevents_passing_verdict(self):
        self.assertTrue(hasattr(self.guest,'mandatory_commands_passed'),'mandatory command verdict missing')
        kinds=['oracle_build','oracle','baseline_build','baseline_probe','project_build','counterexample_build','regression','regression_counterexample','project']
        commands=[{'kind':kind,'state':'passed','exit_code':0,'native_completed':True,'output_truncated':False} for kind in kinds]
        counter=next(row for row in commands if row['kind']=='regression_counterexample')
        counter.update(state='failed',exit_code=101,expected_exit_code=101)
        self.assertTrue(self.guest.mandatory_commands_passed(commands))
        for kind in ('baseline_build','baseline_probe'):
            changed=[dict(row) for row in commands]
            next(row for row in changed if row['kind']==kind).update(state='failed',exit_code=101)
            self.assertFalse(self.guest.mandatory_commands_passed(changed))

    def test_multiline_const_retry_parameter_comments_and_trailing_comma(self):
        template=(ROOT/'integrations/moyai/oracles/counterexample_lib.rs').read_text()
        variants=[('attempt: u32,','attempt'),
                  ('\n /* misleading ) { */ retries /* parameter */ : /* type */ u32, // trailing\n','retries'),
                  ('\n mut retries :\n u32 ,\n','retries')]
        for parameter,name in variants:
            with self.subTest(parameter=parameter):
                declaration='pub\nconst fn bounded_backoff_ms( '+parameter+' ) /* fake { */ -> u64 /* another { */ {'
                body = f' if {name} >= 9 {{ 30_000 }} else {{ 100_u64 << {name} }} '
                candidate = RETRY_CANDIDATE_BASE + declaration + body + '}\n'
                broken=self.guest.counterexample_source(candidate,template)
                self.assertIn(declaration,broken)
                self.assertIn('owner = ?',broken)
                self.assertNotIn('30_000', broken)
                self.assertIn('60_000',broken)
                self.assertIn('100_u64 << '+name,broken)

    def test_cargo_discovery_uses_root_owned_cwd(self):
        self.assertEqual(self.guest.TRUSTED_CWD, Path('/opt/local-repository'))

    def test_atomic_claim_publishes_complete_json_once(self):
        self.assertTrue(hasattr(self.runner, 'claim'), 'atomic complete claim missing')
        path = self.runner.operation_path(CONVERSATION, 'race')
        results = []
        errors = []
        def claim():
            try:
                results.append(self.runner.claim(path, {'state': 'running', 'payload': 'x'*10000}))
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=claim) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertFalse(errors,repr(errors))
        self.assertEqual(len(results),8)
        self.assertEqual(sum(results), 1)
        self.assertEqual(json.loads(path.read_text())['payload'], 'x'*10000)

    def test_status_during_initial_claim_never_overwrites_or_reads_partial_record(self):
        self.imported()
        path=self.runner.operation_path(CONVERSATION,'claim-status')
        entered=threading.Event(); release=threading.Event(); errors=[]
        original=self.guest.save_json
        def delayed(destination,value):
            if destination==path:
                entered.set(); release.wait(2)
            return original(destination,value)
        record={'state':'running','operation_id':'claim-status','owner_identity':self.guest.incarnation(os.getpid()),'commands':[]}
        def claim():
            try: self.runner.claim(path,record)
            except Exception as exc: errors.append(exc)
        with mock.patch.object(self.guest,'save_json',side_effect=delayed):
            thread=threading.Thread(target=claim); thread.start()
            self.assertTrue(entered.wait(1))
            missing=self.request('status',operation_id='claim-status')
            self.assertEqual(missing['state'],'unknown')
            self.assertFalse(missing['tracked'])
            self.assertFalse(path.exists())
            release.set(); thread.join(2)
        self.assertFalse(errors,repr(errors))
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.request('status',operation_id='claim-status')['state'],'running')

    def test_tracking_publication_failure_still_stops_spawned_child(self):
        state=self.temp_path/'publish.json'
        state.write_text(json.dumps({'state':'running'}))
        original=self.guest.save_json; calls=[]
        def fail_once(path,value):
            calls.append(path)
            if len(calls)==2: raise OSError('controlled publication failure')
            return original(path,value)
        with mock.patch.object(self.guest,'save_json',side_effect=fail_once):
            reply=self.runner.run_process([sys.executable,'-c','import time; time.sleep(20)'],self.temp_path,dict(os.environ),1,state)
        self.assertEqual(reply['state'],'unknown')
        self.assertTrue(reply['process_group_stopped'])
        with self.assertRaises(ProcessLookupError): os.killpg(reply['process_group'],0)

    def test_pid_incarnation_mismatch_never_signals(self):
        self.imported()
        path = self.runner.operation_path(CONVERSATION, 'stale')
        path.write_text(json.dumps({'state': 'running', 'operation_id': 'stale', 'owner_pid': os.getpid(), 'process_group': os.getpgrp(), 'owner_identity': {'boot_id':'stale'}, 'process_identity': {'boot_id':'stale'}}))
        with mock.patch.object(self.guest.os, 'killpg') as kill:
            reply = self.request('cancel', operation_id='stale')
        self.assertEqual(reply['state'], 'unknown')
        kill.assert_not_called()

    def test_excess_regression_targets_settle_signed_failure_without_spawn(self):
        project = self.imported()
        for index in range(9):
            (project/f'tests/new_{index}.rs').write_text('#[test] fn new_test() {}')
        reply = self.request('check', operation_id='many', task_id='task', profile='rust-retry', timeout_seconds=1, receipt_key='e'*64)
        self.assertEqual(reply['state'], 'failed')
        self.assertTrue(reply['no_process_started'])
        self.assertTrue(reply['processes_stopped'])
        self.assertIn('receipt_mac', reply)

    def test_descriptor_bounded_regular_read_rejects_fifo_and_hardlinks(self):
        self.assertTrue(hasattr(self.guest, 'read_regular'), 'descriptor reader missing')
        path = self.temp_path/'fifo'
        os.mkfifo(path)
        start = time.monotonic()
        with self.assertRaises(ValueError): self.guest.read_regular(path, 20)
        self.assertLess(time.monotonic()-start, .2)
        path.unlink(); path.write_bytes(b'x'*21)
        with self.assertRaises(ValueError): self.guest.read_regular(path, 20)

    def test_missing_output_eof_has_independent_deadline(self):
        state = self.temp_path/'held.json'
        state.write_text(json.dumps({'state':'running'}))
        start = time.monotonic()
        selector = self.guest.selectors.DefaultSelector()
        def no_events(delay):
            time.sleep(delay)
            return []
        with mock.patch.object(selector, 'select', side_effect=no_events), mock.patch.object(self.guest.selectors, 'DefaultSelector', return_value=selector):
            reply = self.runner.run_process([sys.executable,'-c','pass'], self.temp_path, dict(os.environ), .1, state)
        self.assertLess(time.monotonic()-start, 1.6)
        self.assertEqual(reply['state'], 'unknown')
        self.assertFalse(reply['pipe_drained'])

    @unittest.skipUnless(sys.platform == 'linux', 'requires Linux subreaper and pidfd')
    def test_command_scope_reaps_worker_and_leaves_concurrent_command_running(self):
        from concurrent.futures import ThreadPoolExecutor
        ready = self.temp_path/'ready'
        release = self.temp_path/'release'
        waiting = ('import pathlib,sys,time\nready,release=map(pathlib.Path,sys.argv[1:])\n'
                   'ready.touch()\nwhile not release.exists(): time.sleep(.01)\nprint("finished")')
        state_a = self.temp_path/'a.json'
        state_b = self.temp_path/'b.json'
        for state in (state_a, state_b):
            state.write_text('{"state":"running"}')
        with ThreadPoolExecutor(max_workers=1) as executor:
            concurrent = executor.submit(self.runner.run_process, [sys.executable,'-c',waiting,str(ready),str(release)],
                                         self.temp_path, dict(os.environ), 5, state_b)
            deadline = time.monotonic() + 2
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists())
            try:
                launch_worker = 'import subprocess; print(subprocess.Popen(["/bin/sleep","4"]).pid)'
                result = self.runner.run_process([sys.executable, '-c', launch_worker], self.temp_path,
                                                 dict(os.environ), 3, state_a)
                self.assertEqual(result['state'], 'passed')
                self.assertTrue(self.guest.command_stopped(result))
                self.assertFalse(Path('/proc', result['output'].strip()).exists())
                self.assertFalse(concurrent.done())
            finally:
                release.touch()
            other = concurrent.result(timeout=2)
            self.assertTrue(self.guest.command_stopped(other))
            self.assertEqual(other['output'].strip(), 'finished')

    @unittest.skipUnless(sys.platform == 'linux', 'requires Linux subreaper and pidfd')
    def test_timeout_and_cancel_reap_real_workers_before_return(self):
        from concurrent.futures import ThreadPoolExecutor
        import select
        launch = ('import pathlib,subprocess,sys\n'
                  'worker=subprocess.Popen(["/bin/sleep","6"])\n'
                  'pathlib.Path(sys.argv[1]).write_text(str(worker.pid))\nworker.wait()')
        for reason in ('timeout', 'cancelled'):
            with self.subTest(reason=reason), ThreadPoolExecutor(max_workers=1) as executor:
                state = self.temp_path/(reason+'.json')
                state.write_text('{"state":"running"}')
                worker = self.temp_path/(reason+'.pid')
                future = executor.submit(self.runner.run_process, [sys.executable, '-c', launch, str(worker)],
                                         self.temp_path, dict(os.environ), 1.5 if reason == 'timeout' else 5, state)
                deadline = time.monotonic() + 1
                while (not worker.exists() or not worker.stat().st_size) and time.monotonic() < deadline:
                    time.sleep(.01)
                pid = int(worker.read_text())
                descriptor = os.pidfd_open(pid)
                self.addCleanup(os.close, descriptor)
                if reason == 'cancelled':
                    state.with_suffix('.cancel').touch()
                result = future.result(timeout=3)
                self.assertEqual(result['state'], reason)
                self.assertTrue(self.guest.command_stopped(result))
                self.assertTrue(select.select([descriptor], [], [], 0)[0])
                self.assertFalse(Path('/proc', str(pid)).exists())

    @unittest.skipUnless(sys.platform == 'linux', 'requires Linux subreaper and pidfd')
    def test_loss_of_guest_owner_stops_its_worker(self):
        import select
        state = self.temp_path/'owner.json'
        state.write_text('{"state":"running"}')
        worker_file = self.temp_path/'worker.pid'
        run = ('import os,sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\n'
               'from repository_guest import Guest\n'
               'Guest().run_process([sys.executable,"-c",sys.argv[2],sys.argv[3]],Path.cwd(),dict(os.environ),8,Path(sys.argv[4]))')
        worker = ('import os,sys,time\nfrom pathlib import Path\n'
                  'Path(sys.argv[1]).write_text(str(os.getpid()))\ntime.sleep(6)')
        with subprocess.Popen([sys.executable,'-B','-c',run,str(GUEST.parent),worker,str(worker_file),str(state)]) as owner:
            try:
                deadline = time.monotonic() + 2
                while not worker_file.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(worker_file.exists())
                worker_pid = int(worker_file.read_text())
                worker_fd = os.pidfd_open(worker_pid)
                self.addCleanup(os.close, worker_fd)
            finally:
                owner.kill()
            owner.wait(timeout=1)
            self.assertTrue(select.select([worker_fd], [], [], 2)[0])
        self.assertFalse(json.loads(state.read_text())['processes_stopped'])

    @unittest.skipUnless(sys.platform == 'linux', 'requires Linux command supervisor')
    def test_missing_supervisor_report_fences_operation_and_scratch(self):
        import shutil
        self.imported()
        oracles = self.temp_path/'oracles'
        shutil.copytree(ROOT/'integrations/moyai/oracles', oracles)
        for name in ('Verifier.lock','Baseline.lock'):
            (oracles/name).write_text('test-only lock')
        self.runner.oracles = oracles
        self.guest.TRUSTED_CWD = self.temp_path
        with mock.patch.object(self.guest, '__file__', str(self.temp_path/'missing'/'guest.py')):
            reply = self.request('check', operation_id='lost-supervisor', task_id='task',
                                 profile='rust-retry', timeout_seconds=2, receipt_key='e'*64)
        self.assertEqual(reply['state'], 'unknown')
        self.assertFalse(reply['no_process_started'])
        self.assertFalse(reply['processes_stopped'])
        self.assertEqual(reply['commands'], [])
        directory = self.temp_path/CONVERSATION
        self.assertTrue((directory/'.repository-state/builds/lost-supervisor').is_dir())
        with self.assertRaises(ValueError):
            self.runner.recover_scratch(directory)

    @unittest.skipUnless(sys.platform == 'linux', 'requires Linux wait flags')
    def test_empty_or_unreadable_child_listing_cannot_certify_cleanup(self):
        spec = importlib.util.spec_from_file_location('supervisor', GUEST.with_name('command_supervisor.py'))
        supervisor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(supervisor)
        command = mock.Mock()
        listing = mock.Mock()
        listing.read_bytes.side_effect = [OSError('temporary read error'), b'', b'', b'']
        with mock.patch.object(supervisor, 'CLEANUP_SECONDS', 1), mock.patch.object(supervisor, 'CHILDREN', listing), \
             mock.patch.object(supervisor.time, 'monotonic', side_effect=[0, .1, .5, 1.1]), \
             mock.patch.object(supervisor.os, 'waitpid', return_value=(0, 0)):
            self.assertFalse(supervisor.stop_children(command))
        self.assertEqual(listing.read_bytes.call_count, 2)

    def test_check_rejects_protected_metadata_change_before_execution(self):
        project = self.imported()
        (project/'Cargo.lock').write_text('bad')
        result = self.request('check', operation_id='tamper', task_id='task', profile='rust-retry', timeout_seconds=1)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['commands'], [])


    def test_regression_proofs_classify_mixed_suite_and_reject_supporting_only(self):
        passed={'exit_code':0,'native_completed':True}
        failed={'exit_code':101,'native_completed':True}
        detecting=self.guest.regression_proof('tests/retry.rs','cap',passed,True,failed)
        supporting=self.guest.regression_proof('tests/retry.rs','initial_delay',passed,True,passed)
        self.assertEqual(detecting['classification'],'detecting')
        self.assertEqual(supporting['classification'],'supporting')
        self.assertEqual([detecting['counterexample_exit_code'],supporting['counterexample_exit_code']],[101,0])
        self.assertTrue(self.guest.regression_proofs_passed([detecting,supporting]))
        self.assertFalse(self.guest.regression_proofs_passed([supporting]))
        self.assertIn('does not detect',supporting['reason'])
        for repaired,compiled,counter in [(passed,False,None),(passed,True,{'exit_code':101,'native_completed':False}),
                                          (passed,True,{'exit_code':1,'native_completed':True}),
                                          ({'exit_code':101,'native_completed':False},True,failed)]:
            proof=self.guest.regression_proof('tests/retry.rs','invalid',repaired,compiled,counter)
            self.assertEqual(proof['classification'],'invalid')
            self.assertFalse(self.guest.regression_proofs_passed([detecting,proof]))

    def test_mandatory_commands_allow_complete_mixed_counterexamples_only(self):
        kinds=['oracle_build','oracle','baseline_build','baseline_probe','project_build','counterexample_build','regression','project']
        commands=[{'kind':kind,'state':'passed','exit_code':0,'native_completed':True,'output_truncated':False} for kind in kinds]
        supporting={'kind':'regression_counterexample','state':'passed','exit_code':0,'expected_exit_code':0,'native_completed':True,'output_truncated':False}
        detecting=dict(supporting,state='failed',exit_code=101,expected_exit_code=101)
        self.assertFalse(self.guest.mandatory_commands_passed(commands+[supporting]))
        self.assertTrue(self.guest.mandatory_commands_passed(commands+[detecting,supporting]))
        for change in ({'native_completed':False},{'output_truncated':True},{'exit_code':1}, {'expected_exit_code':101}):
            self.assertFalse(self.guest.mandatory_commands_passed(commands+[detecting,dict(supporting,**change)]))


if __name__ == '__main__':
    unittest.main()
