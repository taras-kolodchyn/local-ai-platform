"""Controller-owned task state. Guest files, model text and plan flags cannot settle a check."""
from contextlib import closing, contextmanager
from hashlib import sha256
import hmac
import json
import re
import secrets
import sqlite3
import time
import uuid


class RepositoryError(ValueError):
    """A repository workflow action failed its preconditions."""


TERMINAL = {'completed', 'failed', 'timed_out', 'cancelled', 'uncertain'}
ARTIFACT_DIR = 'repository-results'
ARTIFACT_PATHS = tuple(f'{ARTIFACT_DIR}/{name}' for name in ('changes.patch', 'test-report.json', 'result.json'))


def verify_artifact(content, digest):
    if sha256(content).hexdigest() != digest:
        raise RepositoryError('Artifact bytes differ from the controller receipt.')


class Ledger:
    def __init__(self, path='/data/local-tasks.db', *, clock=time.time, lifetime=3600,
                 model_limit=60, tool_limit=120, attempt_limit=4):
        self.path = str(path)
        self.clock = clock
        self.lifetime = lifetime
        self.limits = {'model': model_limit, 'tool': tool_limit}
        self.attempt_limit = attempt_limit
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    session_id TEXT PRIMARY KEY, authority TEXT NOT NULL, task_id TEXT NOT NULL UNIQUE,
                    phase TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                    deadline REAL NOT NULL, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations (
                    session_id TEXT NOT NULL, operation_id TEXT NOT NULL, kind TEXT NOT NULL,
                    status TEXT NOT NULL, started REAL NOT NULL, finished REAL, result TEXT,
                    PRIMARY KEY(session_id,operation_id));
                CREATE TABLE IF NOT EXISTS check_keys (
                    session_id TEXT NOT NULL, operation_id TEXT NOT NULL, key TEXT NOT NULL,
                    PRIMARY KEY(session_id,operation_id));
                CREATE TABLE IF NOT EXISTS events (session_id TEXT NOT NULL, phase TEXT NOT NULL, ts REAL NOT NULL);
            """)

    @contextmanager
    def _transaction(self):
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.execute('BEGIN IMMEDIATE')
            with db:
                yield db

    def _save(self, db, task, phase=None):
        now = self.clock()
        if phase and phase != task['phase']:
            db.execute('INSERT INTO events VALUES (?,?,?)', (task['session_id'], phase, now))
            task['phase'] = phase
        db.execute('UPDATE tasks SET phase=?,updated=?,state=? WHERE session_id=?',
                   (task['phase'], now, json.dumps(task), task['session_id']))

    def _task(self, db, session, authority, *, optional=False):
        row = db.execute('SELECT authority,state FROM tasks WHERE session_id=?', (session,)).fetchone()
        if not row and optional:
            return None
        if not row or row[0] != authority:
            raise RepositoryError('No repository task for this conversation authority.')
        return json.loads(row[1])

    def _active(self, db, task):
        if task['deadline'] <= self.clock() and task['phase'] not in TERMINAL:
            if task.get('pending'):
                self._save(db, task, 'uncertain')
            else:
                self._save(db, task, 'timed_out')
            db.commit()
            raise RepositoryError('Repository task deadline expired.')
        if task['phase'] in TERMINAL:
            raise RepositoryError('Repository task is ' + task['phase'] + '.')

    def status(self, session, authority):
        with self._transaction() as db:
            return self._task(db, session, authority, optional=True)

    def owner_status(self, session, owner):
        # Called only after the HTTP handler checks the authenticated native session owner.
        with closing(sqlite3.connect(self.path, timeout=2)) as db:
            row = db.execute('SELECT state FROM tasks WHERE session_id=?', (session,)).fetchone()
            task = json.loads(row[0]) if row else None
            if task and task.get('attached_actor_id') != owner:
                raise RepositoryError('No repository task belongs to this account.')
            return task

    def attach(self, session, authority, binding, actor_id=None):
        with self._transaction() as db:
            task = self._task(db, session, authority, optional=True)
            if task:
                if task['binding']['repository_id'] != binding['repository_id']:
                    raise RepositoryError('This conversation already has a pinned repository.')
                return task
            now = self.clock()
            task = dict(session_id=session, task_id=uuid.uuid4().hex, binding=binding, phase='preparing',
                        created=now, attached_actor_id=actor_id, deadline=now+self.lifetime, attempts=0, tool_calls=0, model_calls=0,
                        citations=[], plan=[], receipts=[], artifacts={}, pending=None, verified_tree=None)
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)',
                       (session, authority, task['task_id'], 'preparing', now, now, task['deadline'], json.dumps(task)))
            db.execute('INSERT INTO events VALUES (?,?,?)', (session, 'preparing', now))
            return task

    def imported(self, session, authority, tree):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            self._active(db, task)
            if task['phase'] != 'preparing' or not tree:
                raise RepositoryError('Import is already settled or lacks a tree hash.')
            task['original_tree'] = tree
            self._save(db, task, 'analyzing')

    def charge(self, session, authority, kind, identity):
        with self._transaction() as db:
            task = self._task(db, session, authority, optional=True)
            if not task:
                return
            if task['phase'] not in TERMINAL:
                self._active(db, task)
            operation = f'{kind}:{identity}'
            if self._operation(db, session, operation):
                return
            key = kind + '_calls'
            if task[key] >= self.limits[kind] or task['deadline'] <= self.clock():
                if task['phase'] not in TERMINAL:
                    self._save(db, task, 'uncertain' if task.get('pending') else 'failed')
                    db.commit()
                raise RepositoryError('Repository ' + kind + ' budget exhausted.')
            task[key] += 1
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?)',
                       (session, operation, kind, 'done', self.clock(), self.clock(), None))
            self._save(db, task)

    def cite(self, session, authority, citations):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            self._active(db, task)
            for citation in citations:
                if citation not in task['citations'] and len(task['citations']) < 64:
                    task['citations'].append(citation)
            self._save(db, task)

    def begin(self, session, authority, items):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            self._active(db, task)
            if task['phase'] == 'implementing':
                return
            if reason := missing_change_evidence(task, items):
                raise RepositoryError(reason)
            task['plan'] = items
            self._save(db, task, 'implementing')

    @staticmethod
    def _operation(db, session, operation):
        return db.execute('SELECT kind,status,result FROM operations WHERE session_id=? AND operation_id=?',
                          (session, operation)).fetchone()

    def operation_kind(self, session, authority, operation):
        with self._transaction() as db:
            self._task(db, session, authority)
            row = self._operation(db, session, operation)
            return row[0] if row else None

    def check_key(self, session, authority, operation):
        with self._transaction() as db:
            self._task(db, session, authority)
            row = db.execute('SELECT key FROM check_keys WHERE session_id=? AND operation_id=?',
                             (session, operation)).fetchone()
            if not row:
                raise RepositoryError('No controller reservation exists for this check.')
            return row[0]

    def _reserve(self, db, task, operation, kind, fingerprint):
        row = self._operation(db, task['session_id'], operation)
        if row:
            if row[0] != kind or json.loads(row[2])['fingerprint'] != fingerprint:
                raise RepositoryError('The operation identity has changed.')
            return False
        self._active(db, task)
        if task['pending']:
            raise RepositoryError('An operation requires settlement.')
        db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?)', (task['session_id'], operation, kind,
                   'pending', self.clock(), None, json.dumps({'fingerprint': fingerprint})))
        task['pending'] = operation
        return True

    def start_write(self, session, authority, operation, fingerprint, before=None, *, turn=None, path=None):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            if task['phase'] not in {'implementing','verified'}:
                raise RepositoryError('Repository writes require the implementing phase.')
            if not self._reserve(db, task, operation, 'write', fingerprint):
                raise RepositoryError('This write already has a reservation and cannot be repeated.')
            task['verified_tree'] = None
            task.update(write_before=before, write_turn=turn, write_path=path, write_after=None,
                        write_dispatch='reserved', write_outcome=None)
            self._save(db, task, 'editing')

    def dispatch_write(self, session, authority, operation, path, before, after):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            self._active(db, task)
            if (task['phase'] != 'editing' or task['pending'] != operation
                    or task.get('write_dispatch') != 'reserved' or task.get('write_path') != path
                    or task.get('write_before') != before):
                raise RepositoryError('Native write lacks a current matching reservation.')
            task.update(write_dispatch='dispatched',write_after=after)
            self._save(db, task)

    def recover_write(self, session, authority, operation, current_turn, tree):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            if task['pending'] != operation or task['phase'] not in {'editing','uncertain'}:
                return
            if task.get('write_turn') == current_turn:
                return
            stage = task.get('write_dispatch')
            if stage == 'reserved':
                result = 'not_dispatched'
            elif stage == 'dispatched' and tree and tree == task.get('write_after') and tree != task.get('write_before'):
                result = 'observed_expected_tree'
            else:
                task['write_outcome'] = 'dispatch_outcome_unknown'
                self._save(db, task, 'uncertain')
                return
            # A late native handler must acquire dispatch_write before touching the filesystem.
            self._settle(db, task, operation)
            task['write_outcome'] = result
            self._save(db, task, 'implementing')

    def _settle(self, db, task, operation):
        if task['pending'] != operation:
            raise RepositoryError('No matching pending operation.')
        task['pending'] = None
        db.execute('UPDATE operations SET status=?,finished=? WHERE session_id=? AND operation_id=?',
                   ('settled', self.clock(), task['session_id'], operation))

    def finish_reserved_write(self, session, authority, operation):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            if task.get('pending') != operation or task.get('write_dispatch') != 'reserved':
                return
            self._settle(db, task, operation)
            task['write_outcome'] = 'not_dispatched'
            self._save(db, task, 'implementing')

    def finish_write(self, session, authority, operation, outcome='confirmed'):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            self._settle(db, task, operation)
            task['write_outcome'] = outcome
            self._save(db, task, 'implementing')

    def start_check(self, session, authority, operation, tree):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            if not self._reserve(db, task, operation, 'check', tree):
                return {'dispatch': False}
            if task['phase'] not in {'analyzing','implementing','verified'}:
                raise RepositoryError('A check cannot begin in this phase.')
            baseline = task['phase'] == 'analyzing'
            if baseline and (tree != task['original_tree'] or task.get('baseline_failed')):
                raise RepositoryError('The baseline must be checked once on the original tree.')
            if not baseline:
                if task['attempts'] >= self.attempt_limit:
                    raise RepositoryError('Verification attempt budget exhausted.')
                task['attempts'] += 1
            db.execute('INSERT INTO check_keys VALUES (?,?,?)', (session, operation, secrets.token_hex(32)))
            task.update(check_tree=tree, check_baseline=baseline, verified_tree=None, check_observation=None)
            self._save(db, task, 'verifying')
            return {'dispatch': True}

    def reusable_check(self, session, authority, tree):
        """Return settled evidence for an unchanged repair; never resume terminal work."""
        with self._transaction() as db:
            task = self._task(db, session, authority)
            self._active(db, task)
            if (task['phase'] not in {'implementing','verified'} or task.get('pending')
                    or len(task['receipts']) < 2):
                return None
            latest = task['receipts'][-1]
            if latest['state'] == 'passed' and (task['phase'] != 'verified' or task['verified_tree'] != tree):
                return None  # Every admitted write invalidates a pass, including identical-byte writes.
            if latest['state'] == 'failed':
                checks, commands = latest.get('checks', {}), latest.get('commands', [])
                if (latest.get('failure_code') != 'regression_requirement' or latest.get('launch_errors')
                        or checks.get('oracle_passed') is not True
                        or checks.get('regression_counterexample_passed') is not False
                        or not commands or any(c['exit_code'] != 0 or not passing_command(c) for c in commands)):
                    return None  # A failed process may recover without a source edit; allow a bounded retry.
            if latest['state'] in {'passed','failed'} and latest['tree_hash'] == tree:
                return latest
            return None

    def uncertain(self, session, authority, operation):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            if task['pending'] == operation:
                self._save(db, task, 'uncertain')

    def observe_check(self, session, authority, operation, observed):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            if task['pending'] != operation or task['phase'] not in {'verifying','uncertain'}:
                return  # A concurrent authenticated completion owns its settled outcome.
            previous = (task.get('check_observation') or {}).get('state')
            if previous == 'tracked_outcome_unknown' or (
                    previous == 'running' and observed in {'not_yet_observed','tracking_unconfirmed'}):
                # A delayed pre-claim poll must not overwrite a later known-running observation.
                return
            if observed == previous:
                return
            task['check_observation'] = {'operation_id':operation, 'state':observed, 'observed_at':self.clock()}
            self._save(db, task, 'uncertain' if observed == 'tracked_outcome_unknown' else None)

    def finish_check(self, session, authority, operation, receipt, fresh_tree):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            if (task['pending'] != operation or task['phase'] not in {'verifying','uncertain'}
                    or receipt.get('task_id') != task['task_id'] or receipt.get('operation_id') != operation
                    or receipt.get('tree_hash') != task['check_tree'] or fresh_tree != task['check_tree']):
                raise RepositoryError('Verification receipt identity or current tree mismatch.')
            key = db.execute('SELECT key FROM check_keys WHERE session_id=? AND operation_id=?',
                             (session, operation)).fetchone()
            if not key or not authentic_receipt(receipt, key[0]):
                raise RepositoryError('Guest verification receipt signature is invalid.')
            outcome = receipt.get('state')
            commands = receipt.get('commands')
            no_launch = (commands == [] and receipt.get('no_process_started') is True
                         and receipt.get('processes_stopped') is True)
            if outcome in {'passed','failed'} and not (outcome == 'failed' and no_launch):
                validate_checks(receipt)
            elif outcome not in {'cancelled','timeout','failed'} or not (no_launch or (
                    isinstance(commands,list) and bool(commands) and receipt.get('processes_stopped') is True
                    and all(cmd.get('process_group_stopped') is True for cmd in commands))):
                raise RepositoryError('Verification has no authenticated stopped outcome.')
            self._settle(db, task, operation)
            task['receipts'].append(receipt)
            task['check_observation'] = {'operation_id':operation, 'state':outcome, 'observed_at':self.clock()}
            if outcome == 'cancelled':
                phase = 'cancelled'
            elif outcome == 'timeout' or self.clock() >= task['deadline']:
                phase = 'timed_out'
            elif task['check_baseline']:
                task['baseline_failed'] = outcome == 'failed' and baseline_violation(receipt)
                phase = 'analyzing' if task['baseline_failed'] else 'failed'
            elif outcome == 'passed':
                task['verified_tree'] = fresh_tree
                phase = 'verified'
            else:
                phase = 'implementing' if task['attempts'] < self.attempt_limit else 'failed'
            self._save(db, task, phase)
            return phase

    def ready_to_finish(self, session, authority, tree):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            self._active(db, task)
            if task['phase'] not in {'verified','delivering'} or task['verified_tree'] != tree or not tree:
                raise RepositoryError('Run trusted checks on the current tree before finalizing.')
            return task

    def stage_delivery(self, session, authority, tree, artifacts, check_operation):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            self._active(db, task)
            if (task['phase'] not in {'verified','delivering'} or tree != task['verified_tree']
                    or not task['receipts'] or task['receipts'][-1]['operation_id'] != check_operation
                    or set(artifacts) != set(ARTIFACT_PATHS)
                    or (task['artifacts'] and task['artifacts'] != artifacts)):
                raise RepositoryError('Finalization requires fresh evidence and the same three artifacts.')
            task.update(artifacts=artifacts, delivery_check=check_operation)
            task.setdefault('files', {})
            self._save(db, task, 'delivering')

    def reserve_file(self, session, authority, path, turn):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            self._active(db, task)
            if task['phase'] != 'delivering' or path not in task['artifacts']:
                raise RepositoryError('No verified artifact is ready for delivery.')
            op = 'artifact:' + path
            row = self._operation(db, session, op)
            if row:
                return json.loads(row[2])['turn'], False
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?)',
                       (session, op, 'artifact', 'pending', self.clock(), None, json.dumps({'turn':turn})))
            return turn, True

    def delivered(self, session, authority, path, record):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            if (task['phase'] != 'delivering' or record.get('name') != path
                    or record.get('sha256') != task['artifacts'].get(path) or not record.get('id')):
                raise RepositoryError('Native file receipt does not match the verified artifact.')
            op = 'artifact:' + path
            if not self._operation(db, session, op):
                raise RepositoryError('Missing artifact reservation.')
            db.execute('UPDATE operations SET status=?,finished=? WHERE session_id=? AND operation_id=?',
                       ('settled', self.clock(), session, op))
            task['files'][path] = record
            self._save(db, task)

    def complete(self, session, authority, tree):
        with self._transaction() as db:
            task = self._task(db, session, authority)
            self._active(db, task)
            if (task['phase'] != 'delivering' or tree != task['verified_tree']
                    or set(task['files']) != set(ARTIFACT_PATHS)):
                raise RepositoryError('All verified artifacts must be available through native Files.')
            self._save(db, task, 'completed')


def authentic_receipt(receipt, key):
    mac = receipt.get('receipt_mac')
    if not isinstance(mac, str) or not re.fullmatch('[a-f0-9]{64}', mac):
        return False
    body = {k:v for k,v in receipt.items() if k != 'receipt_mac'}
    payload = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return hmac.compare_digest(mac, hmac.digest(bytes.fromhex(key), payload, 'sha256').hex())


def missing_change_evidence(task, items):
    if task['phase'] != 'analyzing':
        return 'Repository changes require the analyzing phase.'
    if not any(c.get('path') in {'src/lib.rs', 'README.md', 'docs/architecture-rules.md'} for c in task['citations']):
        return 'Use search_repository to cite the implementation or documented retry policy.'
    if not task.get('baseline_failed'):
        return 'Use run_repository_checks to establish the failing original baseline.'
    if not items:
        return 'No native plan was saved. Use write_plan with pending future steps and a measurable retry ceiling.'
    if not any(has_acceptance_criteria(item['content']) for item in items):
        return 'Update the native plan with a measurable acceptance criterion for the documented retry ceiling.'
    return None


def has_acceptance_criteria(content):
    text = str(content).casefold()
    ceiling = re.search(r'(?<![0-9])30[,_ ]?000(?![0-9])|\b30\s*(seconds?|секунд|s\b)', text)
    subject = re.search(r'retry|backoff|delay|attempt|затримк|повтор|спроб', text)
    expectation = re.search(r'cap|limit|ceil|bounded|maximum|max\b|at most|never exceed|не перевищ|обмеж|меж', text)
    return bool(ceiling and subject and expectation)


def baseline_violation(receipt):
    return receipt.get('checks', {}).get('baseline_policy_violation') is True and any(
        cmd.get('kind') == 'baseline_probe' and cmd['exit_code'] != 0
        and 'legacy_large_attempt_obeys_documented_ceiling ... FAILED' in cmd.get('output','')
        and re.search(r'left:\s*60000', cmd['output']) and re.search(r'right:\s*30000', cmd['output'])
        for cmd in receipt['commands'])


ORACLE_TESTS = ['legacy_entry_point_retains_shared_policy_compatibility', 'shared_policy_caps_without_overflow',
                'shared_policy_is_monotonic_and_bounded', 'shared_policy_starts_at_one_hundred_ms']


def validate_checks(receipt):
    checks = receipt.get('checks', {})
    commands = receipt.get('commands', [])
    if (receipt.get('profile') != 'rust-retry' or checks.get('protected_unchanged') is not True
            or checks.get('tree_unchanged') is not True or checks.get('verification_inputs_unchanged') is not True
            or not commands
            or any(not isinstance(cmd.get('argv'), list) or not cmd['argv']
                   or type(cmd.get('exit_code')) is not int for cmd in commands)):
        raise RepositoryError('Trusted verification commands or protected-tree evidence missing.')
    if receipt['state'] == 'passed':
        counts = ('oracle_tests_run', 'project_tests_run', 'regression_tests_added', 'regression_tests_run')
        if (receipt.get('ok') is not True or checks.get('oracle_passed') is not True
                or checks.get('regression_counterexample_passed') is not True
                or checks.get('protected_test_names') != ORACLE_TESTS
                or not regression_evidence_valid(checks, commands)
                or any(type(checks.get(key)) is not int or checks[key] < 1 for key in counts)
                or checks['oracle_tests_run'] < 4
                or not {'project','oracle','baseline_probe','regression','regression_counterexample'} <= {cmd.get('kind') for cmd in commands}
                or any(not passing_command(cmd) for cmd in commands)):
            raise RepositoryError('Passing verification requires executed project, regression and oracle tests.')


def passing_command(cmd):
    builds = {'oracle_build','baseline_build','project_build','counterexample_build'}
    if cmd.get('process_group_stopped') is not True:
        return False
    if cmd.get('kind') not in builds and cmd.get('output_truncated') is not False:
        return False
    if cmd.get('kind') == 'regression_counterexample':
        return (type(cmd.get('exit_code')) is int and cmd['exit_code'] in (0,101)
                and cmd.get('expected_exit_code') == cmd['exit_code']
                and cmd.get('state') == ('failed' if cmd['exit_code'] else 'passed')
                and cmd.get('native_completed') is True)
    return cmd['exit_code'] == 0


def regression_evidence_valid(checks, commands):
    """Bind every added identity to compiled, complete candidate/counter runs."""
    proofs = checks.get('regression_proofs')
    if not isinstance(proofs, list) or not proofs:
        return False
    if any(type(checks.get(key)) is not int or checks[key] != len(proofs)
           for key in ('regression_tests_added', 'regression_tests_run')):
        return False
    identities = set()
    detecting = False
    for proof in proofs:
        if not isinstance(proof, dict):
            return False
        path, name = proof.get('path'), proof.get('test_name')
        if (not isinstance(path,str) or not re.fullmatch(r'tests/[A-Za-z0-9_-]{1,80}\.rs',path)
                or not isinstance(name,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_:]*',name)
                or (path,name) in identities):
            return False
        identities.add((path,name))
        exit_code = proof.get('counterexample_exit_code')
        if (type(exit_code) is not int or exit_code not in (0,101)
                or proof.get('classification') != ('detecting' if exit_code else 'supporting')
                or proof.get('counterexample_failed') is not (exit_code == 101)
                or any(proof.get(key) is not True for key in
                       ('repaired_passed','counterexample_compiled','counterexample_completed'))):
            return False
        detecting |= exit_code == 101
        builds = [cmd for cmd in commands if cmd.get('kind')=='counterexample_build' and cmd.get('test_path')==path]
        if len(builds)!=1 or not passing_command(builds[0]):
            return False
        for kind, expected in (('regression',0),('regression_counterexample',exit_code)):
            runs = [cmd for cmd in commands if cmd.get('kind')==kind
                    and cmd.get('test_path')==path and cmd.get('test_names')==[name]]
            if len(runs)!=1:
                return False
            run = runs[0]
            if (not passing_command(run) or run.get('exit_code')!=expected
                    or run.get('expected_exit_code')!=expected or run.get('native_completed') is not True
                    or run.get('state')!=('failed' if expected else 'passed')):
                return False
            output = run.get('output','')
            records = re.findall(r'^test ([^\n]+?) \.\.\. ([^\n]+)$',output,re.MULTILINE)
            summaries = re.findall(r'^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;',output,re.MULTILINE)
            wanted = ('FAILED','0','1','0','0') if expected else ('ok','1','0','0','0')
            if records != [(name,wanted[0])] or len(summaries)!=1 or summaries[0][:5]!=wanted:
                return False
    return (detecting and all(sum(cmd.get('kind')==kind for cmd in commands)==len(proofs)
                             for kind in ('regression','regression_counterexample')))
