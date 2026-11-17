"""Probe failure cleanup tests require only the standard library."""
import asyncio
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[2] / 'integrations/moyai/tests/repository_probe_cleanup.py'


def load():
    spec = importlib.util.spec_from_file_location('probe_cleanup', PATH)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class FakeLedger:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE task (state TEXT)')
            db.execute('CREATE TABLE check_keys (key TEXT)')
            db.execute('INSERT INTO check_keys VALUES (?)', ('private-reconciliation-key',))
            db.execute('INSERT INTO task VALUES (?)', (json.dumps({'phase': 'verifying',
                'pending': 'probe-check', 'binding': {}, 'receipts': []}),))
    def status(self, *scope):
        with closing(sqlite3.connect(self.path)) as db, db:
            return json.loads(db.execute('SELECT state FROM task').fetchone()[0])
    def operation_kind(self, *scope):
        return 'check'
    def stop(self):
        task = self.status(); task.update(pending=None, phase='cancelled', receipts=[{'processes_stopped': True}])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE task SET state=?', (json.dumps(task),))


class FakeBackend:
    def __init__(self, ledger, fail=False):
        self.ledger, self.fail, self.cancel_calls = ledger, fail, []
    async def status(self, scope):
        if self.fail:
            raise TimeoutError('private transport failure')
        return {'state': 'running'}
    def snapshot(self, binding):
        return {}, None
    async def cancel(self, scope, operation, manifest):
        self.cancel_calls.append(operation)
        if self.fail:
            raise RuntimeError('private cancellation failure')
        self.ledger.stop()


class ProbeCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_assertion_after_running_check_cancels_and_preserves_failed_ledger(self):
        module = load()
        with tempfile.TemporaryDirectory() as temporary:
            evidence = module.ProbeEvidence(Path(temporary))
            backend = FakeBackend(FakeLedger(evidence.ledger_path))
            removed = []
            async def remove(scope): removed.append(scope)
            scope = ('exclusive-probe', 'private-authority')
            with self.assertRaisesRegex(AssertionError, 'original probe assertion'):
                try:
                    self.assertEqual((await backend.status(scope))['state'], 'running')
                    raise AssertionError('original probe assertion')
                except BaseException as failure:
                    await module.finish_probes(backend, [scope], remove, evidence, failure=failure)
                    raise
            self.assertEqual(backend.cancel_calls, ['probe-check'])
            self.assertEqual(removed, [scope])
            self.assertTrue(evidence.ledger_path.exists())
            self.assertEqual(evidence.directory.stat().st_mode & 0o777, 0o700)
            for name in ('ledger.db', 'metadata.json'):
                self.assertEqual((evidence.directory/name).stat().st_mode & 0o777, 0o600)
            metadata = json.loads((evidence.directory/'metadata.json').read_text())
            self.assertEqual(metadata['state'], 'failed')
            self.assertTrue(metadata['cleanup'][0]['stopped'])

    async def test_reconcile_and_cancel_failure_retains_keys_and_original_failure(self):
        module = load()
        with tempfile.TemporaryDirectory() as temporary:
            evidence = module.ProbeEvidence(Path(temporary))
            backend = FakeBackend(FakeLedger(evidence.ledger_path), fail=True)
            removed = []
            async def remove(scope): removed.append(scope)
            original = AssertionError('original remains visible')
            with self.assertRaisesRegex(AssertionError, 'original remains visible') as raised:
                try:
                    raise original
                finally:
                    await module.finish_probes(backend, [('exclusive', 'authority')], remove, evidence, failure=original)
            self.assertIs(raised.exception, original)
            self.assertEqual(removed, [])
            self.assertEqual(backend.ledger.status()['pending'], 'probe-check')
            with closing(sqlite3.connect(evidence.ledger_path)) as db, db:
                self.assertEqual(db.execute('SELECT key FROM check_keys').fetchone()[0], 'private-reconciliation-key')
            self.assertIn('private reconciliation evidence', original.__notes__[0])
            metadata = json.loads((evidence.directory/'metadata.json').read_text())
            self.assertFalse(metadata['cleanup'][0]['stopped'])
            self.assertEqual(metadata['failure_class'], 'AssertionError')

    async def test_cleanup_failure_without_original_error_is_not_a_pass(self):
        module = load()
        with tempfile.TemporaryDirectory() as temporary:
            evidence = module.ProbeEvidence(Path(temporary))
            backend = FakeBackend(FakeLedger(evidence.ledger_path), fail=True)
            async def remove(scope): self.fail('unknown workspace must remain')
            with self.assertRaises(module.ProbeCleanupError):
                await module.finish_probes(backend, [('exclusive', 'authority')], remove, evidence)
            self.assertTrue(evidence.ledger_path.exists())

    async def test_metadata_failure_does_not_replace_original_failure(self):
        from unittest.mock import patch
        module = load()
        with tempfile.TemporaryDirectory() as temporary:
            evidence = module.ProbeEvidence(Path(temporary))
            backend = FakeBackend(FakeLedger(evidence.ledger_path), fail=True)
            async def remove(scope): self.fail('unknown workspace must remain')
            original = AssertionError('original assertion survives metadata failure')
            with patch.object(evidence, 'record', side_effect=PermissionError('evidence update denied')):
                await module.finish_probes(backend, [('exclusive', 'authority')], remove, evidence, failure=original)
            self.assertTrue(evidence.ledger_path.exists())
            self.assertTrue(original.__notes__)

    async def test_success_removes_settled_workspace_and_retains_private_audit(self):
        from contextlib import redirect_stdout
        import io
        module = load()
        with tempfile.TemporaryDirectory() as temporary:
            evidence = module.ProbeEvidence(Path(temporary))
            backend = FakeBackend(FakeLedger(evidence.ledger_path))
            removed = []
            async def remove(scope): removed.append(scope)
            output = io.StringIO()
            with redirect_stdout(output):
                await module.finish_probes(backend, [('exclusive', 'private-authority')], remove, evidence)
            self.assertEqual(removed, [('exclusive', 'private-authority')])
            self.assertTrue(evidence.ledger_path.exists())
            self.assertEqual(json.loads((evidence.directory/'metadata.json').read_text())['state'], 'cleaned')
            self.assertEqual(output.getvalue(), '')

    async def test_uncertain_scope_does_not_skip_later_exclusive_scope(self):
        from types import SimpleNamespace
        module = load()
        with tempfile.TemporaryDirectory() as temporary:
            evidence = module.ProbeEvidence(Path(temporary))
            states = {'bad': {'phase': 'verifying', 'pending': 'pending-check', 'binding': {}, 'receipts': []},
                      'good': {'phase': 'analyzing', 'pending': None, 'receipts': []}}
            async def unavailable(*args): raise TimeoutError('unavailable')
            backend = SimpleNamespace(ledger=SimpleNamespace(status=lambda session, authority: states[session],
                operation_kind=lambda *args: 'check'), status=unavailable, cancel=unavailable,
                snapshot=lambda binding: ({}, None))
            removed = []
            async def remove(scope): removed.append(scope)
            await module.finish_probes(backend, [('bad', 'one'), ('good', 'two')], remove, evidence,
                failure=AssertionError('original failure'))
            self.assertEqual(removed, [('good', 'two')])
            metadata = json.loads((evidence.directory/'metadata.json').read_text())
            self.assertEqual(metadata['state'], 'uncertain')
            self.assertEqual(len(metadata['cleanup']), 2)
            self.assertFalse(metadata['cleanup'][0]['stopped'])
            self.assertTrue(metadata['cleanup'][1]['removed'])
