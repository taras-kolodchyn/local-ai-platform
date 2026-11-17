import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'integrations' / 'moyai'))
from repository_ledger import Ledger, RepositoryError, verify_artifact, validate_checks


def add_regression(receipt, name='added', counter_exit=101, path='tests/added.rs'):
    checks = receipt['checks']
    checks.setdefault('regression_proofs', []).append({
        'path':path, 'test_name':name, 'classification':'detecting' if counter_exit else 'supporting',
        'repaired_passed':True, 'counterexample_compiled':True, 'counterexample_completed':True,
        'counterexample_exit_code':counter_exit, 'counterexample_failed':counter_exit==101})
    checks['regression_tests_added'] = checks['regression_tests_run'] = len(checks['regression_proofs'])
    if not any(c.get('kind')=='counterexample_build' and c.get('test_path')==path for c in receipt['commands']):
        receipt['commands'].append({'kind':'counterexample_build','test_path':path,'argv':['cargo','test','--no-run'],
                                    'exit_code':0,'state':'passed','output_truncated':False,'process_group_stopped':True})
    for kind, code in (('regression',0),('regression_counterexample',counter_exit)):
        state='FAILED' if code else 'ok'
        receipt['commands'].append({'kind':kind,'test_path':path,'test_names':[name], 'argv':['/native/test','--exact',name],
                                    'exit_code':code,'expected_exit_code':code,'state':'failed' if code else 'passed',
                                    'native_completed':True,'output_truncated':False,'process_group_stopped':True,
                                    'output':f'test {name} ... {state}\n\ntest result: {state}. {int(not code)} passed; {int(bool(code))} failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\n'})


class RepositoryLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 100
        self.ledger = Ledger(Path(self.tmp.name) / 'tasks.db', clock=lambda: self.now,
                             tool_limit=2, model_limit=1, lifetime=20)
        self.binding = dict(repository_id='repo', snapshot_id='snapshot', generation_id='generation',
                            store_id='store', verification_profile='rust-retry')
        self.ledger.attach('s', 'a', self.binding)
        self.ledger.imported('s', 'a', 'base')

    def test_reattach_pins_generation_and_authority(self):
        task = self.ledger.attach('s', 'a', dict(self.binding, generation_id='new'))
        self.assertEqual(task['binding'], self.binding)
        with self.assertRaises(RepositoryError):
            self.ledger.status('s', 'other')

    def test_mutation_requires_evidence_and_is_invalidated_at_admission(self):
        with self.assertRaises(RepositoryError):
            self.ledger.begin('s', 'a', [])
        with self.assertRaises(RepositoryError):
            self.ledger.start_write('s', 'a', 'w', 'hash')

    def test_budget_is_durable_and_retries_are_deduplicated(self):
        self.ledger.charge('s', 'a', 'model', 'm1')
        self.ledger.charge('s', 'a', 'model', 'm1')
        with self.assertRaises(RepositoryError):
            self.ledger.charge('s', 'a', 'model', 'm2')
        self.assertEqual(self.ledger.status('s', 'a')['phase'], 'failed')

    def test_expired_admission_is_terminal(self):
        self.now = 121
        with self.assertRaises(RepositoryError):
            self.ledger.charge('s', 'a', 'tool', 't')
        self.assertEqual(self.ledger.status('s', 'a')['phase'], 'timed_out')

    def test_duplicate_check_never_dispatches_twice_and_unknown_fences_writes(self):
        self.assertTrue(self.ledger.start_check('s', 'a', 'op', 'base')['dispatch'])
        self.assertFalse(self.ledger.start_check('s', 'a', 'op', 'base')['dispatch'])
        with self.assertRaises(RepositoryError):
            self.ledger.start_check('s', 'a', 'another', 'base')
        self.ledger.uncertain('s', 'a', 'op')
        self.assertEqual(self.ledger.status('s', 'a')['phase'], 'uncertain')
        with self.assertRaises(RepositoryError):
            self.ledger.start_check('s', 'a', 'another', 'base')

    def test_fake_or_stale_checks_cannot_certify(self):
        self.ledger.start_check('s', 'a', 'op', 'base')
        with self.assertRaises(RepositoryError):
            self.ledger.finish_check('s', 'a', 'op', {'state':'passed','tree_hash':'base'}, 'changed')
        with self.assertRaises(RepositoryError):
            self.ledger.ready_to_finish('s', 'a', 'base')

    def test_download_hash_compares_actual_bytes(self):
        digest = hashlib.sha256(b'controller bytes').hexdigest()
        verify_artifact(b'controller bytes', digest)
        with self.assertRaises(RepositoryError):
            verify_artifact(b'other', digest)

    def receipt(self, operation, tree, outcome, *, baseline=False, missing_regression=False):
        import hmac
        import json
        commands = []
        for kind in ('project','baseline_probe','oracle'):
            failed = baseline and kind in ('baseline_probe','oracle')
            commands.append({'kind':kind,'argv':['/opt/rust/bin/cargo','test','--offline','--locked'],
                             'exit_code':101 if kind == 'regression_counterexample' else int(failed), 'native_completed':True,
                             'expected_exit_code':101 if kind == 'regression_counterexample' else 0,'output_truncated':False,
                             'output':'legacy_large_attempt_obeys_documented_ceiling ... FAILED\nleft: 60000\nright: 30000'
                             if failed else 'test result: ok', 'process_group_stopped': True})
        from repository_ledger import ORACLE_TESTS
        receipt = {'task_id':self.ledger.status('s','a')['task_id'], 'operation_id':operation,
                   'profile':'rust-retry','tree_hash':tree, 'state':outcome, 'ok':outcome == 'passed',
                   'commands':commands,'checks':{'protected_unchanged':True,'tree_unchanged':True,
                       'verification_inputs_unchanged':True, 'protected_test_names':ORACLE_TESTS,
                       'regression_counterexample_passed':True,'regression_proofs':[], 'baseline_policy_violation':baseline,
                       'project_tests_run':3,'oracle_tests_run':4,'regression_tests_added':1,
                       'regression_tests_run':1,'oracle_passed':not baseline}}
        if missing_regression:
            receipt['failure_code'] = 'regression_requirement'
            receipt['checks'].update(regression_tests_added=0,regression_tests_run=0,
                                     regression_counterexample_passed=False)
        else:
            add_regression(receipt)
        key = self.ledger.check_key('s','a',operation)
        receipt['receipt_mac'] = hmac.digest(bytes.fromhex(key), json.dumps(
            receipt,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode(),'sha256').hex()
        return receipt

    def implement(self):
        self.ledger.start_check('s','a','baseline','base')
        self.ledger.finish_check('s','a','baseline',self.receipt('baseline','base','failed',baseline=True),'base')
        self.ledger.cite('s','a',[{'path':'src/lib.rs','start_line':2,'end_line':4,'chunk_id':'chunk'}])
        self.ledger.begin('s','a',[{'content':'Verify regression tests cap the worst delay at 30000 ms.'}])
        self.ledger.start_write('s','a','w','fingerprint')
        self.ledger.finish_write('s','a','w')

    def test_recovered_pass_requires_valid_signature_and_unchanged_tree(self):
        self.implement()
        self.ledger.start_check('s','a','good','repaired')
        receipt = self.receipt('good','repaired','passed')
        self.ledger.uncertain('s','a','good')
        forged = dict(receipt, commands=[])
        for invalid, tree in [(forged,'repaired'),(receipt,'stale')]:
            with self.assertRaises(RepositoryError):
                self.ledger.finish_check('s','a','good',invalid,tree)
        restarted = Ledger(self.ledger.path,clock=lambda: self.now)
        restarted.finish_check('s','a','good',receipt,'repaired')
        self.assertEqual(restarted.ready_to_finish('s','a','repaired')['phase'],'verified')
        restarted.start_write('s','a','edit-again','new')
        with self.assertRaises(RepositoryError):
            restarted.ready_to_finish('s','a','repaired')

    def test_unchanged_repair_reuses_latest_authenticated_receipt_without_attempt(self):
        self.implement()
        self.ledger.start_check('s','a','repair','repaired')
        receipt = self.receipt('repair','repaired','failed',missing_regression=True)
        self.ledger.finish_check('s','a','repair',receipt,'repaired')
        before = self.ledger.status('s','a')
        for _ in range(6):
            self.assertEqual(self.ledger.reusable_check('s','a','repaired'), receipt)
        self.assertEqual(self.ledger.status('s','a'), before)
        self.assertIsNone(self.ledger.reusable_check('s','a','edited'))
        restarted = Ledger(self.ledger.path,clock=lambda: self.now)
        self.assertEqual(restarted.reusable_check('s','a','repaired'),receipt)

    def test_check_reuse_never_bypasses_authority_pending_terminal_or_deadline(self):
        self.implement()
        self.assertIsNone(self.ledger.reusable_check('s','a','base'))
        self.ledger.start_check('s','a','repair','repaired')
        receipt = self.receipt('repair','repaired','passed')
        self.assertIsNone(self.ledger.reusable_check('s','a','repaired'))
        self.ledger.finish_check('s','a','repair',receipt,'repaired')
        self.assertEqual(self.ledger.reusable_check('s','a','repaired'),receipt)
        with self.assertRaises(RepositoryError):
            self.ledger.reusable_check('s','foreign','repaired')
        self.ledger.start_write('s','a','new-write','fingerprint')
        self.assertIsNone(self.ledger.reusable_check('s','a','repaired'))
        self.ledger.uncertain('s','a','new-write')
        with self.assertRaises(RepositoryError):
            self.ledger.reusable_check('s','a','repaired')
        self.ledger.recover_write('s','a','new-write','different-turn',None)
        self.now += 4000
        with self.assertRaises(RepositoryError):
            self.ledger.reusable_check('s','a','repaired')
        self.assertEqual(self.ledger.status('s','a')['phase'],'timed_out')

    def test_admitted_write_invalidates_pass_even_if_bytes_are_unchanged(self):
        self.implement()
        self.ledger.start_check('s','a','repair','repaired')
        self.ledger.finish_check('s','a','repair',self.receipt('repair','repaired','passed'),'repaired')
        self.ledger.start_write('s','a','same-bytes','fingerprint')
        self.ledger.finish_write('s','a','same-bytes')
        self.assertIsNone(self.ledger.reusable_check('s','a','repaired'))
        with self.assertRaises(RepositoryError):
            self.ledger.ready_to_finish('s','a','repaired')

    def test_failed_command_does_not_prevent_unchanged_tree_retry(self):
        from repository_guest import sign_receipt
        self.implement()
        self.ledger.start_check('s','a','repair','repaired')
        receipt = self.receipt('repair','repaired','failed',missing_regression=True)
        receipt['commands'][0]['exit_code'] = 137
        receipt.pop('receipt_mac')
        receipt = sign_receipt(receipt,self.ledger.check_key('s','a','repair'))
        self.ledger.finish_check('s','a','repair',receipt,'repaired')
        self.assertIsNone(self.ledger.reusable_check('s','a','repaired'))
        self.assertTrue(self.ledger.start_check('s','a','retry-after-resource-recovery','repaired')['dispatch'])

    def test_partial_or_unclassified_failure_is_not_reused(self):
        from repository_guest import sign_receipt
        self.implement()
        for index, extra in enumerate(({'launch_errors':[{'type':'BlockingIOError','message':'EAGAIN'}]},
                                       {'failure_code':None,'error':'temporary result read failure'})):
            operation, tree = f'repair-{index}',f'tree-{index}'
            self.ledger.start_check('s','a',operation,tree)
            receipt = self.receipt(operation,tree,'failed',missing_regression=True)
            receipt.update(extra)
            receipt.pop('receipt_mac')
            receipt = sign_receipt(receipt,self.ledger.check_key('s','a',operation))
            self.ledger.finish_check('s','a',operation,receipt,tree)
            self.assertIsNone(self.ledger.reusable_check('s','a',tree))

    def test_native_file_reservations_survive_lost_delivery_and_require_all_downloads(self):
        from repository_ledger import ARTIFACT_PATHS
        self.implement()
        self.ledger.start_check('s','a','good','repaired')
        self.ledger.finish_check('s','a','good',self.receipt('good','repaired','passed'),'repaired')
        files = {p:'digest-'+str(i) for i,p in enumerate(ARTIFACT_PATHS)}
        self.ledger.stage_delivery('s','a','repaired',files,'good')
        for path, digest in files.items():
            self.assertEqual(self.ledger.reserve_file('s','a',path,'turn'),('turn', True))
            self.assertEqual(self.ledger.reserve_file('s','a',path,'new-turn'),('turn', False))
            with self.assertRaises(RepositoryError):
                self.ledger.complete('s','a','repaired')
            with self.assertRaises(RepositoryError):
                self.ledger.delivered('s','a',path,{'sha256':'wrong','name':path,'id':'native'})
            self.ledger.delivered('s','a',path,{'sha256':digest,'name':path,'id':'native'})
        self.ledger.complete('s','a','repaired')
        self.assertEqual(self.ledger.status('s','a')['phase'],'completed')

    def test_cannot_use_zero_or_incomplete_validation_as_success(self):
        from repository_ledger import validate_checks
        self.ledger.start_check('s','a','op','base')
        verified = self.receipt('op','base','passed')
        for change in [{'regression_tests_run':0},{'oracle_tests_run':0},{'protected_unchanged':False},
                       {'tree_unchanged':False},{'verification_inputs_unchanged':False}]:
            with self.subTest(change=change), self.assertRaises(RepositoryError):
                validate_checks(dict(verified,checks=verified['checks'] | change))

    def test_plan_requires_profile_specific_measurable_criterion(self):
        from repository_ledger import has_acceptance_criteria
        for item in ('Acceptance criteria', 'Write acceptance criteria later',
                     'Acceptance criteria: the page has a title', 'Write a test someday'):
            with self.subTest(item=item):
                self.assertFalse(has_acceptance_criteria(item))
        self.assertTrue(has_acceptance_criteria('Test that large retry attempts never exceed 30,000 ms.'))

    def test_staging_fences_exact_verified_receipt_before_artifact_io(self):
        from repository_ledger import ARTIFACT_PATHS
        self.implement()
        self.ledger.start_check('s','a','first','tree')
        self.ledger.finish_check('s','a','first',self.receipt('first','tree','passed'),'tree')
        self.ledger.start_check('s','a','second','tree')
        self.ledger.finish_check('s','a','second',self.receipt('second','tree','passed'),'tree')
        with self.assertRaises(RepositoryError):
            self.ledger.stage_delivery('s','a','tree',dict.fromkeys(ARTIFACT_PATHS,'hash'),'first')
        self.ledger.stage_delivery('s','a','tree',dict.fromkeys(ARTIFACT_PATHS,'hash'),'second')
        with self.assertRaises(RepositoryError):
            self.ledger.start_write('s','a','late-write','fingerprint')

    def test_old_reserved_write_can_be_closed_and_late_dispatch_cannot_write(self):
        self.implement()
        self.ledger.start_write('s','a','next','args','before',turn='old',path='src/lib.rs')
        self.ledger.recover_write('s','a','next','fresh','before')
        self.assertEqual(self.ledger.status('s','a')['phase'],'implementing')
        with self.assertRaises(RepositoryError):
            self.ledger.dispatch_write('s','a','next','src/lib.rs','before','after')

    def test_unknown_dispatched_write_is_only_settled_by_proven_postimage(self):
        self.implement()
        self.ledger.start_write('s','a','next','args','before',turn='old',path='src/lib.rs')
        self.ledger.dispatch_write('s','a','next','src/lib.rs','before','after')
        self.ledger.recover_write('s','a','next','fresh','before')
        self.assertEqual(self.ledger.status('s','a')['phase'],'uncertain')
        self.ledger.recover_write('s','a','next','fresh','after')
        self.assertEqual(self.ledger.status('s','a')['phase'],'implementing')

    def test_empty_cancel_requires_explicit_authenticated_no_spawn_evidence(self):
        import hmac
        import json
        self.ledger.start_check('s','a','cancel','base')
        task = self.ledger.status('s','a')
        key = bytes.fromhex(self.ledger.check_key('s','a','cancel'))
        receipt = {'state':'cancelled','profile':'rust-retry','tree_hash':'base','task_id':task['task_id'],
                   'operation_id':'cancel','commands':[],'no_process_started':False,'processes_stopped':True}
        def signed(r):
            return dict(r, receipt_mac=hmac.digest(key,json.dumps(r,sort_keys=True,separators=(',',':')).encode(),'sha256').hex())
        for invalid in [receipt, dict(receipt,no_process_started=True,processes_stopped=False)]:
            with self.assertRaises(RepositoryError):
                self.ledger.finish_check('s','a','cancel',signed(invalid),'base')
        valid = signed(dict(receipt,no_process_started=True))
        self.assertEqual(self.ledger.finish_check('s','a','cancel',valid,'base'),'cancelled')

    def test_out_of_order_missing_poll_does_not_fence_an_observed_running_check(self):
        self.ledger.start_check('s','a','queued','base')
        self.ledger.observe_check('s','a','queued','running')
        self.ledger.observe_check('s','a','queued','not_yet_observed')
        task = self.ledger.status('s','a')
        self.assertEqual((task['phase'],task['check_observation']['state']),('verifying','running'))

    def test_mixed_detecting_and_supporting_regressions_pass(self):
        self.ledger.start_check('s','a','mix','base')
        receipt = self.receipt('mix','base','passed')
        add_regression(receipt, 'supporting', 0)
        validate_checks(receipt)

    def test_supporting_only_regression_suite_fails(self):
        self.ledger.start_check('s','a','mix','base')
        receipt = self.receipt('mix','base','passed')
        receipt['commands'] = [c for c in receipt['commands'] if c.get('kind') not in ('regression','regression_counterexample')]
        receipt['checks']['regression_proofs'] = []
        add_regression(receipt, 'supporting', 0)
        with self.assertRaises(RepositoryError):
            validate_checks(receipt)

    def test_mixed_suite_rejects_invalid_supporting_evidence(self):

        mutations = {
            'classification_lie': lambda r,p,c,b: p.update(classification='detecting'),
            'proof_exit_lie': lambda r,p,c,b: p.update(counterexample_exit_code=101),
            'proof_failed_lie': lambda r,p,c,b: p.update(counterexample_failed=True),
            'compile_failed': lambda r,p,c,b: b.update(exit_code=101,state='failed'),
            'uncompiled_proof': lambda r,p,c,b: p.update(counterexample_compiled=False),
            'missing_build': lambda r,p,c,b: r['commands'].remove(b),
            'missing_counter': lambda r,p,c,b: r['commands'].remove(c),
            'wrong_path': lambda r,p,c,b: c.update(test_path='tests/other.rs'),
            'wrong_identity': lambda r,p,c,b: c.update(test_names=['other']),
            'actual_compile_error': lambda r,p,c,b: c.update(exit_code=101,expected_exit_code=101,state='failed',output='compiler error'),
            'truncated': lambda r,p,c,b: c.update(output_truncated=True),
            'incomplete': lambda r,p,c,b: c.update(native_completed=False),
            'no_tests': lambda r,p,c,b: c.update(output='test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;'),
            'ignored': lambda r,p,c,b: c.update(output='test supporting ... ignored\ntest result: ok. 0 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out;'),
            'missing_summary': lambda r,p,c,b: c.update(output='test supporting ... ok'),
            'duplicate_record': lambda r,p,c,b: c.update(output=c['output']+'test supporting ... ok\n'),
            'duplicate_summary': lambda r,p,c,b: c.update(output=c['output']+c['output'].split('\n\n')[1]),
            'unexpected_exit': lambda r,p,c,b: c.update(exit_code=1,expected_exit_code=1,state='failed'),
            'exit_state_mismatch': lambda r,p,c,b: c.update(state='failed'),
            'boolean_exit': lambda r,p,c,b: c.update(exit_code=False),
            'unstopped': lambda r,p,c,b: c.update(process_group_stopped=False),
            'extra_counter': lambda r,p,c,b: r['commands'].append(dict(c)),
            'duplicate_proof': lambda r,p,c,b: r['checks']['regression_proofs'].append(dict(p)),
            'wrong_count': lambda r,p,c,b: r['checks'].update(regression_tests_added=99),
            'candidate_failure': lambda r,p,c,b: next(x for x in r['commands'] if x.get('kind')=='regression' and x.get('test_names')==['supporting']).update(exit_code=101),
        }

        self.ledger.start_check('s','a','mix','base')
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                receipt = self.receipt('mix','base','passed')
                add_regression(receipt, 'supporting', 0)
                proof = receipt['checks']['regression_proofs'][-1]
                counter = next(c for c in receipt['commands'] if c.get('kind')=='regression_counterexample' and c.get('test_names')==['supporting'])
                build = next(c for c in receipt['commands'] if c.get('kind')=='counterexample_build')
                mutate(receipt,proof,counter,build)
                with self.assertRaises(RepositoryError):
                    validate_checks(receipt)

    def test_same_identity_in_distinct_targets_keeps_separate_evidence(self):
        self.ledger.start_check('s','a','mix','base')
        receipt = self.receipt('mix','base','passed')
        add_regression(receipt, 'added', 0, path='tests/supporting.rs')
        validate_checks(receipt)
