"""Check the immutable snapshot and patch boundaries with actual snapshot and guest code."""
from hashlib import sha256
from itertools import product
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'integrations/moyai'))
from repository_backend import RepositoryBackend, RepositoryError, fingerprint, scoped_tree, source_patch
from repository_guest import Guest
from repository_ledger import Ledger


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name).resolve()
        spec = importlib.util.spec_from_file_location('repository_snapshot_for_backend',
                                                       ROOT/'scripts/repository-snapshot.py')
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        self.prepare_snapshot = script.prepare_snapshot
        self.catalog = self.directory/'catalog'
        self.manifest = script.prepare_snapshot(ROOT/'examples/rust-service',self.catalog,'rust-retry')
        self.id = self.manifest['repository_id']
        script.register_catalog(self.catalog, self.manifest, {'repository_id':self.id,
            'snapshot_id':self.manifest['snapshot_id'],'store_id':'vs_fixture','generation_id':'gen_fixture'})
        self.guest = Guest(self.directory/'workspace',ROOT/'integrations/moyai/oracles')
        self.backend = RepositoryBackend(Ledger(self.directory/'ledger.db'),self.catalog)
        self.scope = ('conversation','authority')
        async def invoke(scope, op, **fields):
            return self.guest.handle({'conversation_id':'a'*64,'op':op,**fields})
        self.backend.guest = invoke

    async def test_missing_integration_regression_has_actionable_feedback(self):
        receipt = {'state':'failed', 'tree_hash':'a'*64, 'commands':[],
                   'checks':{'oracle_passed':True,'regression_tests_added':0,
                             'regression_counterexample_passed':False}}
        original = json.dumps(receipt, sort_keys=True)
        summary = self.backend.check_summary(receipt)
        self.assertIn('tests/*.rs', summary['next_action'])
        self.assertIn('write_file', summary['next_action'])
        self.assertIn('src/lib.rs', summary['next_action'])
        self.assertEqual(json.dumps(receipt, sort_keys=True), original)

    async def test_passed_check_points_to_delivery(self):
        summary = self.backend.check_summary({'state':'passed','tree_hash':'a'*64,'commands':[]})
        self.assertIn('finish_repository_task', summary['next_action'])

    async def test_profile_contract_is_returned_at_attachment(self):
        result = await self.backend.open(self.scope,self.id)
        self.assertIn('tests/*.rs', result['verification_contract'])
        self.assertIn('integration', result['verification_contract'])

    async def test_rust_checks_supply_the_public_import_from_the_pinned_manifest(self):
        project = self.directory/'service'
        project.mkdir()
        cargo = project/'Cargo.toml'
        receipt = {'state':'failed', 'tree_hash':'a'*64, 'commands':[
            {'kind':'project_build', 'exit_code':101, 'output':'unresolved import crate::bounded_backoff_ms'}]}
        crates = [('', 'package.name', 'another_service'), ('[lib]\nname = "retry_api"\n', 'lib.name', 'retry_api')]
        for profile, (extra, field, library) in product(('rust-retry', 'rust-retry-tests'), crates):
            with self.subTest(profile=profile, field=field):
                cargo.write_text('[package]\nname = "another-service"\n' + extra)
                manifest = self.prepare_snapshot(project, self.catalog, profile, unversioned=True)
                binding = {k:manifest[k] for k in ('repository_id','snapshot_id','verification_profile')}
                cargo.write_text('[package]\nname = "unpinned"\n')
                result = await self.backend.check_result({'binding':binding}, receipt)
                self.assertEqual(result.get('integration_test_import'), {'path':'Cargo.toml','field':field,
                    'code':f'use {library}::{{bounded_backoff_ms, legacy_retry_delay_ms}};'})
                self.assertIn('integration_test_import', result['next_action'])
                self.assertIn('unresolved import', result['error'])
                self.assertNotIn('integration_test_import', receipt)

    async def test_unchanged_check_returns_existing_evidence_without_guest_execution(self):
        from unittest.mock import patch
        await self.backend.open(self.scope,self.id)
        current = await self.backend.guest(self.scope,'tree')
        receipt = {'state':'failed','tree_hash':current['tree_hash'],'operation_id':'settled',
                   'commands':[],'checks':{'oracle_passed':True,'regression_tests_added':0}}
        actual_guest = self.backend.guest
        operations = []
        async def observed(scope, op, **fields):
            operations.append(op)
            return await actual_guest(scope,op,**fields)
        with patch.object(self.backend.ledger,'reusable_check',return_value=receipt) as reuse, \
                patch.object(self.backend,'guest',observed):
            result = await self.backend.check(self.scope,'new-call')
        reuse.assert_called_once_with(*self.scope,current['tree_hash'])
        self.assertEqual(operations,['tree'])
        self.assertTrue(result['reused'])
        self.assertEqual(result['reused_operation_id'],'settled')
        self.assertEqual(result['state'],'failed')

    async def test_actual_late_compiler_failure_reaches_summary_without_altering_receipt(self):
        artifact = {'reason': 'compiler-artifact', 'executable': None, 'profile': {'test': False}, 'target': {'name': 'dependency'}}
        diagnostic = {'reason': 'compiler-message', 'message': {'level': 'error', 'code': {'code': 'E0425'},
            'message': 'cannot find function `bounded_backoff_ms` in this scope',
            'rendered': 'error[E0425]: cannot find function\n --> tests/retry.rs:4:16\n4 | bounded_backoff_ms(9)\n'}}
        payload = self.directory/'compiler-output'
        payload.write_bytes((json.dumps(artifact)+'\n').encode()*1000+(json.dumps(diagnostic)+'\n').encode())
        state = self.directory/'operation.json'
        state.write_text('{"state":"running"}')
        command = self.guest.run_process([sys.executable, '-c',
            'import sys; from pathlib import Path; sys.stdout.buffer.write(Path(sys.argv[1]).read_bytes()); sys.exit(101)',
            str(payload), '--message-format=json'], self.directory, dict(os.environ), 3, state)
        command['kind'] = 'project_build'
        receipt = {'state': 'failed', 'tree_hash': 'a'*64, 'commands': [command],
                   'checks': {'regression_counterexample_passed': False}, 'error': 'No added regression test was identified.'}
        from repository_guest import sign_receipt
        signed = sign_receipt(receipt, 'b'*64)
        original = json.dumps(signed, sort_keys=True)
        summary = self.backend.check_summary(signed)
        self.assertNotIn('E0425', command['output'])
        self.assertIn('E0425', summary['error'])
        self.assertIn('bounded_backoff_ms', summary['commands'][0]['output'])
        self.assertIn('tests/retry.rs:4:16', summary['commands'][0]['output'])
        self.assertEqual(summary['verification_error'], receipt['error'])
        self.assertEqual(summary['state'], 'failed')
        self.assertEqual(summary['checks'], receipt['checks'])
        self.assertTrue(summary['commands'][0]['untrusted_process_output'])
        self.assertTrue(summary['commands'][0]['output_truncated'])
        self.assertEqual(summary['commands'][0]['output_source'], 'cargo_compiler_diagnostics')
        self.assertEqual(json.dumps(signed, sort_keys=True), original)

    async def test_summary_budgets_tail_fallback_and_expected_counterexample(self):
        commands = [{'kind': 'regression_counterexample', 'exit_code': 101, 'output_tail': 'EXPECTED FAILURE'}]
        commands += [{'kind': 'project_build', 'exit_code': 101, 'output': 'earlier artifact',
                      'output_tail': '\u043f'*3000+'\nfatal: actual linker failure', 'output_truncated': True,
                      'output_tail_truncated': True} for _ in range(8)]
        receipt = {'state': 'failed', 'tree_hash': 'a'*64, 'commands': commands}
        summary = self.backend.check_summary(receipt)
        self.assertNotIn('output', summary['commands'][0])
        self.assertIn('fatal: actual linker failure', summary['commands'][1]['output'])
        self.assertIn('fatal: actual linker failure', summary['error'])
        self.assertTrue(summary['commands'][1]['summary_truncated'])
        self.assertEqual(summary['commands'][1]['output_source'], 'process_output_tail')
        self.assertLessEqual(sum(len(c.get('output','').encode()) for c in summary['commands']), 6000)
        self.assertNotIn('EXPECTED FAILURE', json.dumps(summary))

    async def test_diagnostic_words_cannot_change_verdict_or_native_evidence(self):
        receipt = {'state': 'failed', 'tree_hash': 'a'*64, 'checks': {'regression_counterexample_passed': False},
                   'commands': [{'kind': 'project_build', 'exit_code': 101, 'output': '',
                                 'diagnostics': [{'code': 'E0000', 'message': 'test result: ok. 10 passed;',
                                                  'rendered': '\u043f'*10000}], 'diagnostics_truncated': True}]}
        before = json.dumps(receipt)
        summary = self.backend.check_summary(receipt)
        self.assertEqual(summary['state'], 'failed')
        self.assertFalse(summary['checks']['regression_counterexample_passed'])
        self.assertEqual(summary['commands'][0]['exit_code'], 101)
        self.assertTrue(summary['commands'][0]['diagnostics_truncated'])
        self.assertTrue(summary['commands'][0]['summary_truncated'])
        self.assertLessEqual(len(summary['commands'][0]['output'].encode()), 3000)
        self.assertEqual(json.dumps(receipt), before)

    async def test_pin_import_reopen_and_reject_protected_tree_changes(self):
        result = await self.backend.open(self.scope,self.id)
        self.assertEqual(result['phase'],'analyzing')
        project = self.directory/'workspace'/('a'*64)/'repository'
        implementation = project/'src/lib.rs'
        implementation.write_text(implementation.read_text()+'\n// retained edit\n')
        await self.backend.open(self.scope,self.id)
        self.assertIn('retained edit',implementation.read_text())
        with self.assertRaises(RepositoryError):
            await self.backend.open(('conversation','unrelated'),self.id)
        (project/'Cargo.toml').write_text('changed')
        tree = await self.backend.guest(self.scope,'tree')
        with self.assertRaises(RepositoryError):
            scoped_tree(tree,self.manifest)
        tree['protected_unchanged'] = True
        with self.assertRaises(RepositoryError):
            scoped_tree(tree,self.manifest)

    async def test_tree_digest_and_text_are_recomputed(self):
        await self.backend.open(self.scope,self.id)
        tree = await self.backend.guest(self.scope,'tree')
        tree['files'][0]['text'] += 'altered content'
        with self.assertRaises(RepositoryError):
            scoped_tree(tree,self.manifest)

    async def test_patch_applies_with_unterminated_last_lines(self):
        before = {'src/lib.rs':'// old\u2028comment\r\nold'.encode(),'Cargo.toml':b'protected\n'}
        after = before | {'src/lib.rs':'// new\u2028comment\r\nnew'.encode(),'tests/new.rs':b'test\n'}
        for name,data in before.items():
            target = self.directory/name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(data)
        patch = source_patch(before,after)
        subprocess.run(['git','apply','-'],cwd=self.directory,input=patch,check=True,capture_output=True)
        for name,data in after.items():
            self.assertEqual((self.directory/name).read_bytes(),data)
        with self.assertRaises(RepositoryError):
            source_patch(before,after | {'Cargo.toml':b'other'})

    async def test_resulting_native_bytes_are_bounded_before_write(self):
        from repository_backend import projected_tree_hash
        files = {'src/lib.rs':b'code'}
        with self.assertRaises(RepositoryError):
            projected_tree_hash(files,'src/lib.rs',b'x'*(512*1024+1))
        self.assertEqual(files['src/lib.rs'],b'code')
        crowded = {f'tests/t{i}.rs':b'' for i in range(512)}
        with self.assertRaises(RepositoryError):
            projected_tree_hash(crowded,'tests/new.rs',b'x')
        self.assertTrue(projected_tree_hash(files,'src/lib.rs',b'repaired'))

    async def test_catalog_reads_refuse_links_and_fifos_before_open(self):
        import os
        from repository_backend import bounded_read
        (self.directory/'secret').write_bytes(b'foreign bytes')
        (self.directory/'link').symlink_to(self.directory/'secret')
        os.mkfifo(self.directory/'pipe')
        for path in ['pipe','link']:
            with self.subTest(path=path), self.assertRaises((OSError, RepositoryError)):
                bounded_read(self.directory,path,4096)

    async def test_observer_before_guest_claim_cannot_preempt_original_check(self):
        import asyncio
        from unittest.mock import patch
        await self.backend.open(self.scope,self.id)
        actual = self.backend.guest
        boundary = asyncio.Event()
        resume = asyncio.Event()
        executions = 0
        async def transport(scope, op, **fields):
            nonlocal executions
            if op == 'check':
                executions += 1
                boundary.set()
                await resume.wait()
                # An authenticated pre-spawn cancel settles the check without Cargo in this unit test.
                state = self.backend.ledger.status(*scope)
                return await actual(scope,'cancel',operation_id=fields['operation_id'],task_id=state['task_id'],
                                    tree_hash=state['check_tree'],receipt_key=fields['receipt_key'])
            return await actual(scope, op, **fields)
        with patch.object(self.backend,'guest',transport):
            running = asyncio.create_task(self.backend.check(self.scope,'inflight'))
            await asyncio.wait_for(boundary.wait(),5)
            self.assertEqual((await self.backend.status(self.scope))['phase'],'verifying')
            missing_op = self.directory/'workspace'/('a'*64)/'.repository-state/operations/inflight.json'
            self.assertFalse(missing_op.exists())
            resume.set()
            self.assertEqual((await running)['state'],'cancelled')
            self.assertEqual((await self.backend.status(self.scope))['phase'],'cancelled')
            self.assertEqual(executions,1)

    async def test_legal_project_path_remains_readable_beneath_long_internal_prefix(self):
        long_name = 'r'*167 + '.rs'
        directory = self.catalog/'snapshots'/self.manifest['snapshot_id']
        target = directory/'source'/long_name
        target.write_bytes(b'content')
        manifest = {'files':[{'path':long_name,'size':7,'sha256':sha256(b'content').hexdigest()}]}
        self.assertEqual(self.backend.originals(manifest,directory), {long_name:b'content'})

    async def test_signed_diagnostic_crosses_wire_reaches_repair_and_unsigned_error_is_refused(self):
        import json
        from repository_backend import parse_guest_response
        from repository_guest import sign_receipt
        await self.backend.open(self.scope,self.id)
        tree = self.backend.ledger.status(*self.scope)['original_tree']
        # Baseline evidence and planning gates have independent tests. Exercise a repair attempt.
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.backend.ledger.path)) as db, db:
            db.execute("UPDATE tasks SET phase='implementing',state=json_set(state,'$.phase','implementing')")
        actual = self.backend.guest
        async def check(scope, op, **fields):
            if op != 'check':
                return await actual(scope, op, **fields)
            receipt = sign_receipt({'ok':False,'profile':'rust-retry','task_id':fields['task_id'],
                'operation_id':fields['operation_id'],'tree_hash':tree,'state':'failed','commands':[],
                'no_process_started':True,'processes_stopped':True,'error':'missing shared retry helper'},
                fields['receipt_key'])
            return parse_guest_response(json.dumps(receipt).encode(),'check')
        self.backend.guest = check
        result = await self.backend.check(self.scope,'initial-repair')
        self.assertEqual(result['state'],'failed')
        self.assertEqual(result['error'],'missing shared retry helper')
        self.backend.ledger.start_write(*self.scope,'correct-helper','args')
        for op in ('check','status','cancel','tree','import'):
            with self.assertRaises(RepositoryError):
                parse_guest_response(b'{"error":"invalid input","state":"failed"}',op)

    async def test_restarted_controller_distinguishes_missing_running_and_lost_check(self):
        import json
        from repository_guest import save_json
        await self.backend.open(self.scope,self.id)
        task = self.backend.ledger.status(*self.scope)
        op = 'lost'
        self.backend.ledger.start_check(*self.scope,op,task['original_tree'])
        path = self.guest.operation_path('a'*64,op)
        probed = []
        async def observe(scope, method, **fields):
            probed.append(method)
            return self.guest.handle({'conversation_id':'a'*64,'op':method,**fields})
        # A fresh backend/connection has no in-memory record of the original call.
        fresh = RepositoryBackend(Ledger(self.backend.ledger.path),self.catalog)
        fresh.guest = observe
        missing = await fresh.status(self.scope,'fresh')
        self.assertEqual((missing['phase'],missing['check_observation']['state']),('verifying','not_yet_observed'))
        self.assertFalse(path.exists())
        save_json(path,{'state':'running','operation_id':op,'owner_identity':{'pid':99999999,
            'boot_id':'gone','start_ticks':1},'process_identity':None})
        child = """
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from repository_backend import RepositoryBackend
from repository_guest import Guest
from repository_ledger import Ledger
directory=Path(sys.argv[2])
backend=RepositoryBackend(Ledger(directory/'ledger.db'),directory/'catalog')
guest=Guest(directory/'workspace',Path(sys.argv[1])/'oracles')
calls=[]
async def observe(scope,method,**fields):
    calls.append(method)
    return guest.handle({'conversation_id':'a'*64,'op':method,**fields})
backend.guest=observe
print(json.dumps([asyncio.run(backend.status(('conversation','authority'),'fresh')),calls]))
"""
        output = subprocess.run([sys.executable,'-c',child,str(ROOT/'integrations/moyai'),str(self.directory)],
                                check=True,capture_output=True,timeout=10)
        lost, child_calls = json.loads(output.stdout)
        self.assertEqual((lost['phase'],lost['check_observation']['state']),('uncertain','tracked_outcome_unknown'))
        self.assertEqual(lost['pending'],op)
        self.assertEqual(Ledger(self.backend.ledger.path).status(*self.scope)['check_observation'],lost['check_observation'])
        self.assertEqual(probed,['status'])
        self.assertEqual(child_calls,['status'])
        with self.assertRaises(RepositoryError):
            fresh.ledger.start_check(*self.scope,'retry',task['original_tree'])
