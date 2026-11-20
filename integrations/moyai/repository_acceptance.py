"""Host acceptance assertions over executed tools, signed checks and current bytes."""
import re


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_evidence(task, observer, result, report, completed_tools, binding, downloaded_hashes, policy_lines):
    require(task.get('phase') == 'completed' and task.get('pending') is None,
            'The controller task is not completed and settled')
    require(task.get('task_id') and task['task_id'] == result.get('task_id') == report.get('task_id')
            and report.get('verification_profile') == 'rust-retry', 'Downloaded result identifies another task or profile')
    require(all(result.get(key) == task.get(key) for key in ('citations','plan','attempts')),
            'Downloaded result changed plan, citations or attempt count')
    paths = {'repository-results/changes.patch','repository-results/test-report.json'}
    require(set(result.get('artifacts',{})) == paths and set(downloaded_hashes) == paths
            and all(result['artifacts'][path] == downloaded_hashes[path] == task.get('artifacts',{}).get(path)
                    for path in paths), 'Result references different patch or report bytes')
    for key in ('repository_id', 'snapshot_id', 'generation_id'):
        require(task.get('binding', {}).get(key) == binding[key] == result.get('binding', {}).get(key),
                'Source or index generation changed')
    require(task.get('verified_tree') == observer.get('tree_hash') == result.get('verified_tree')
            and task.get('original_tree') == result.get('original_tree')
            and result.get('outcome') == 'passed', 'Current source differs from verified result')
    require(observer.get('uid') == 10001 and observer.get('protected_unchanged') is True,
            'Independent workspace or protected-input observation failed')
    receipts = task.get('receipts') or []
    require(len(receipts) >= 2 and observer.get('receipt_authenticity') == [True] * len(receipts),
            'Every check must have independently authenticated evidence')
    require(report.get('baseline_and_attempts') == receipts, 'Downloaded report differs from controller evidence')
    require(all(receipt.get('processes_stopped') is True and all(
                cmd.get('descendants_stopped') is True and cmd.get('process_group_stopped') is True
                and cmd.get('pipe_drained') is True for cmd in receipt['commands']) for receipt in receipts),
            'A verification command lacks evidence of descendant cleanup or drained output')
    baseline, final = receipts[0], receipts[-1]
    require(baseline.get('state') == 'failed' and baseline.get('tree_hash') == task.get('original_tree')
            and baseline.get('checks', {}).get('baseline_policy_violation') is True,
            'Original source did not demonstrate the documented policy violation')
    require(any(command.get('kind') == 'baseline_probe' and command.get('exit_code') != 0
                and 'legacy_large_attempt_obeys_documented_ceiling ... FAILED' in command.get('output', '')
                and re.search(r'left:\s*60000', command['output'])
                and re.search(r'right:\s*30000', command['output'])
                for command in baseline.get('commands', [])), 'Baseline probe was not actually executed')
    require(final.get('state') == 'passed' and final.get('tree_hash') == task.get('verified_tree'),
            'Final verification is missing or stale')
    checks = final.get('checks', {})
    require(all(checks.get(key) is True for key in ('protected_unchanged', 'tree_unchanged',
                'verification_inputs_unchanged', 'oracle_passed', 'regression_counterexample_passed')),
            'Independent checks or regression counterexample failed')
    commands = final.get('commands') or []
    validate_regressions(checks, commands)
    require({'project', 'oracle', 'baseline_probe', 'regression', 'regression_counterexample'}
            <= {c.get('kind') for c in commands}
            and all(c.get('exit_code') in ((0,101) if c.get('kind') == 'regression_counterexample' else (0,))
                    and (c.get('kind') != 'regression_counterexample'
                         or (c.get('native_completed') is True and c.get('expected_exit_code') == c['exit_code']
                             and c.get('state') == ('failed' if c['exit_code'] else 'passed')))
                    and type(c.get('exit_code')) is int
                    and (c.get('output_truncated') is False or c.get('kind') in
                         {'oracle_build','baseline_build','project_build','counterexample_build'})
                    and c.get('process_group_stopped') is True
                    for c in commands), 'Validation command did not complete successfully')
    require(task.get('citations') and task.get('plan'), 'Cited context or native plan is missing')
    policy_citations = [c for c in task['citations'] if c.get('path') == 'docs/architecture-rules.md'
                        and type(c.get('start_line')) is int and type(c.get('end_line')) is int
                        and any(1 <= c['start_line'] <= line <= c['end_line'] for line in policy_lines)]
    require(policy_citations, 'Retrieved context does not cover the documented retry policy')
    intake = 'open_github_issue' if task.get('github_issue') else 'open_repository'
    required = {intake, 'search_repository', 'write_plan', 'run_repository_checks',
                'begin_repository_changes', 'finish_repository_task'}
    require(required <= completed_tools and completed_tools & {'edit_file', 'write_file'},
            'Actual model tools did not complete the repository workflow')
    return policy_citations


def validate_regressions(checks, commands):
    # Independently reconstruct the identity/result relation from the signed
    # command records, rather than accepting the producer's classification.
    proofs = checks.get('regression_proofs')
    require(isinstance(proofs,list) and bool(proofs), 'No added regression proofs')
    require(all(type(checks.get(key)) is int and checks[key]==len(proofs)
                for key in ('regression_tests_added','regression_tests_run')),
            'Added regression identities were not all executed')
    seen = set()
    failures = 0
    for proof in proofs:
        require(isinstance(proof,dict), 'Malformed regression proof')
        path, name = proof.get('path'), proof.get('test_name')
        require(isinstance(path,str) and re.fullmatch(r'tests/[A-Za-z0-9_-]{1,80}\.rs',path)
                and isinstance(name,str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_:]*',name),
                'Malformed regression identity')
        require((path,name) not in seen, 'Duplicate regression identity')
        seen.add((path,name))
        builds = [c for c in commands if c.get('kind')=='counterexample_build' and c.get('test_path')==path]
        require(len(builds)==1 and type(builds[0].get('exit_code')) is int and builds[0]['exit_code']==0
                and builds[0].get('process_group_stopped') is True,
                'Same-API counterexample did not compile')
        counter_exit = None
        for kind in ('regression','regression_counterexample'):
            matches = [c for c in commands if c.get('kind')==kind and c.get('test_path')==path
                       and c.get('test_names')==[name]]
            require(len(matches)==1, 'Missing or duplicate exact regression execution')
            run = matches[0]
            code = run.get('exit_code')
            require(type(code) is int and code in ((0,) if kind=='regression' else (0,101))
                    and run.get('native_completed') is True and run.get('output_truncated') is False
                    and run.get('expected_exit_code')==code and run.get('process_group_stopped') is True
                    and run.get('state')==('failed' if code else 'passed'),
                    'Regression did not complete successfully on candidate or counterexample')
            expected_state = 'FAILED' if code else 'ok'
            records = re.findall(r'^test ([^\n]+?) \.\.\. ([^\n]+)$',run.get('output',''),re.MULTILINE)
            summaries = re.findall(r'^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;',run.get('output',''),re.MULTILINE)
            require(records==[(name,expected_state)] and len(summaries)==1
                    and summaries[0][:5]==(expected_state,'0' if code else '1','1' if code else '0','0','0'),
                    'Regression native result is missing, ignored, truncated or malformed')
            if kind=='regression_counterexample':
                counter_exit = code
        classification = 'detecting' if counter_exit==101 else 'supporting'
        require(all(proof.get(key) is True for key in
                    ('repaired_passed','counterexample_compiled','counterexample_completed'))
                and type(proof.get('counterexample_exit_code')) is int
                and proof['counterexample_exit_code']==counter_exit
                and proof.get('counterexample_failed') is (counter_exit==101)
                and proof.get('classification')==classification,
                'Regression proof disagrees with actual counterexample result')
        failures += counter_exit==101
    require(all(sum(c.get('kind')==kind for c in commands)==len(proofs)
                for kind in ('regression','regression_counterexample')),
            'Regression command has no matching proof')
    require(failures>0, 'Added tests only support the suite; none detects the 60,000 ms defect')
