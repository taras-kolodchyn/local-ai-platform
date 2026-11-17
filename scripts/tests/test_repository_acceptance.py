"""Acceptance cannot pass from model text or a stale successful test report."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


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


class RepositoryAcceptanceTests(unittest.TestCase):
    def validator(self):
        path = ROOT / 'integrations/moyai/repository_acceptance.py'
        self.assertTrue(path.is_file(), 'Repository acceptance assertions are missing')
        spec = importlib.util.spec_from_file_location('repository_acceptance', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.validate_evidence

    def evidence(self):
        binding = {'repository_id': 'repo_fixture', 'snapshot_id': 'snapshot', 'generation_id': 'generation'}
        baseline = {'state': 'failed', 'tree_hash': 'original', 'operation_id': 'baseline',
                    'checks': {'baseline_policy_violation': True},
                    'commands': [{'kind': 'baseline_probe', 'exit_code': 101,
                                  'output': 'legacy_large_attempt_obeys_documented_ceiling ... FAILED\nleft: 60000\nright: 30000'}]}
        final = {'state': 'passed', 'tree_hash': 'verified', 'operation_id': 'final',
                 'checks': {'protected_unchanged': True, 'tree_unchanged': True,
                            'verification_inputs_unchanged': True, 'oracle_passed': True,
                            'regression_counterexample_passed': True,
                            'regression_proofs': []},
                 'commands': [{'kind': kind, 'exit_code': 0, 'output_truncated': False,
                               'process_group_stopped': True}
                              for kind in ('oracle', 'project', 'baseline_probe')]}
        add_regression(final)
        final['commands'].append({'kind': 'project_build', 'exit_code': 0,
                                  'output_truncated': True, 'process_group_stopped': True})
        task = {'task_id':'task_fixture','phase': 'completed', 'binding': binding, 'original_tree': 'original',
                'verified_tree': 'verified', 'pending': None, 'receipts': [baseline, final],
                'citations': [{'path': 'docs/architecture-rules.md', 'start_line': 5, 'end_line': 10}],
                'plan': [{'content': 'Retry delays never exceed 30,000 ms'}], 'attempts':1}
        observer = {'tree_hash': 'verified', 'receipt_authenticity': [True, True],
                    'protected_unchanged': True, 'uid': 10001}
        hashes = {'repository-results/changes.patch':'a'*64,'repository-results/test-report.json':'b'*64}
        task['artifacts'] = dict(hashes)
        result = {'task_id':'task_fixture','binding': binding, 'original_tree': 'original',
                  'verified_tree': 'verified', 'outcome': 'passed', 'attempts':1,
                  'plan':copy.deepcopy(task['plan']), 'citations':copy.deepcopy(task['citations']), 'artifacts':dict(hashes)}
        report = {'task_id':'task_fixture','verification_profile':'rust-retry','baseline_and_attempts': task['receipts']}
        tools = {'open_repository', 'search_repository', 'write_plan', 'run_repository_checks',
                 'begin_repository_changes', 'write_file', 'finish_repository_task'}
        return task, observer, result, report, tools, binding, hashes

    def test_requires_real_baseline_and_current_authenticated_result(self):
        self.validator()(*self.evidence())

    def test_rejects_forged_stale_or_incomplete_success_evidence(self):
        changes = [lambda e: e[1].update(tree_hash='stale'),
                   lambda e: e[1].update(receipt_authenticity=[True, False]),
                   lambda e: e[0].update(phase='implementing'),
                   lambda e: e[0]['receipts'][0]['commands'][0].update(output='FAILED'),
                   lambda e: e[0]['receipts'][-1]['checks'].update(regression_counterexample_passed=False),
                   lambda e: e[0]['receipts'][-1]['commands'][0].update(process_group_stopped=False),
                   lambda e: e[0]['receipts'][-1]['commands'][-2].update(exit_code=0),
                   lambda e: e[2].update(verified_tree='stale'),
                   lambda e: e[4].remove('search_repository'),
                   lambda e: e[0].update(citations=[]),
                   lambda e: e[0]['citations'][0].update(path='README.md'),
                   lambda e: e[0]['citations'][0].update(start_line=8),
                   lambda e: e[0].update(plan=[]),
                   lambda e: e[3].update(baseline_and_attempts=[]),
                   lambda e: e[2].update(task_id='foreign-task'),
                   lambda e: e[3].update(task_id='foreign-task'),
                   lambda e: e[3].update(verification_profile='other'),
                   lambda e: e[2].update(plan=[]),
                   lambda e: e[2].update(citations=[]),
                   lambda e: e[2].update(attempts=10),
                   lambda e: e[2].update(artifacts={}),
                   lambda e: e[6].update({'repository-results/changes.patch':'c'*64})]
        check = self.validator()
        for change in changes:
            with self.subTest(change=change):
                evidence = copy.deepcopy(self.evidence())
                change(evidence)
                with self.assertRaises(ValueError):
                    check(*evidence)

    def test_mixed_detecting_and_supporting_regressions_pass(self):
        evidence = self.evidence()
        receipt = evidence[0]['receipts'][-1]
        add_regression(receipt, 'supporting', 0)
        self.validator()(*evidence)

    def test_supporting_only_regression_suite_fails(self):
        evidence = self.evidence()
        receipt = evidence[0]['receipts'][-1]
        receipt['commands'] = [c for c in receipt['commands'] if c.get('kind') not in ('regression','regression_counterexample')]
        receipt['checks']['regression_proofs'] = []
        add_regression(receipt, 'supporting', 0)
        with self.assertRaises(ValueError):
            self.validator()(*evidence)

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

        for label, mutate in mutations.items():
            with self.subTest(label=label):
                evidence = self.evidence()
                receipt = evidence[0]['receipts'][-1]
                add_regression(receipt, 'supporting', 0)
                proof = receipt['checks']['regression_proofs'][-1]
                counter = next(c for c in receipt['commands'] if c.get('kind')=='regression_counterexample' and c.get('test_names')==['supporting'])
                build = next(c for c in receipt['commands'] if c.get('kind')=='counterexample_build')
                mutate(receipt,proof,counter,build)
                with self.assertRaises(ValueError):
                    self.validator()(*evidence)

    def test_same_identity_in_distinct_targets_keeps_separate_evidence(self):
        evidence = self.evidence()
        receipt = evidence[0]['receipts'][-1]
        add_regression(receipt, 'added', 0, path='tests/supporting.rs')
        self.validator()(*evidence)
