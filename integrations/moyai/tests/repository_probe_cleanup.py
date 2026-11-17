"""Private, durable cleanup for operator-created runtime probes; stdlib only."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import uuid


class ProbeCleanupError(RuntimeError):
    pass


class ProbeEvidence:
    def __init__(self, root=Path('/data/repository-probes')):
        root = Path(root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise ValueError('Unsafe probe evidence directory')
        root.chmod(0o700)
        self.directory = root / uuid.uuid4().hex
        self.directory.mkdir(mode=0o700)
        self.ledger_path = self.directory / 'ledger.db'
        descriptor = os.open(self.ledger_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        self.record([], 'running')

    def record(self, scopes, state, *, cleanup=None, failure=None):
        data = {'schema_version': 1, 'state': state,
                'scopes': [{'session': scope[0], 'authority': scope[1]} for scope in scopes],
                'cleanup': cleanup or [], 'failure_class': type(failure).__name__ if failure else None}
        descriptor, temporary = tempfile.mkstemp(prefix='.metadata-', dir=self.directory)
        try:
            with os.fdopen(descriptor, 'w') as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(data, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / 'metadata.json')
        finally:
            Path(temporary).unlink(missing_ok=True)

async def _stop_scope(backend, scope):
    errors = []
    task = backend.ledger.status(*scope)
    if task and task.get('pending'):
        operation = task['pending']
        if backend.ledger.operation_kind(*scope, operation) != 'check':
            return False, ['unreconciled_non_check_operation']
        try:
            await asyncio.wait_for(backend.status(scope), timeout=35)
        except Exception as exc:
            errors.append(type(exc).__name__)
        task = backend.ledger.status(*scope)
        if task.get('pending'):
            try:
                manifest, _ = await asyncio.to_thread(backend.snapshot, task['binding'])
                await backend.cancel(scope, operation, manifest)
            except Exception as exc:
                errors.append(type(exc).__name__)
            # A lost cancel reply may still have a settled, signed outcome.
            try:
                await asyncio.wait_for(backend.status(scope), timeout=35)
            except Exception as exc:
                errors.append(type(exc).__name__)
        task = backend.ledger.status(*scope)
    stopped = bool(task and not task.get('pending') and
        task.get('phase') in {'analyzing', 'implementing', 'verified', 'completed',
                              'failed', 'timed_out', 'cancelled'} and
        all(receipt.get('processes_stopped') is True for receipt in task.get('receipts', [])))
    return stopped, errors


async def finish_probes(backend, scopes, remove_workspace, evidence, *, failure=None):
    """Inspect every exclusive scope; retain durable authority for any unknown work.

    ``failure`` is the exception already propagating from the probe body. Cleanup
    diagnostics never replace it, and private evidence exists before remote work.
    """
    results = []
    for scope in scopes:
        row = {'session': scope[0], 'stopped': False, 'removed': False, 'errors': []}
        try:
            row['stopped'], row['errors'] = await asyncio.wait_for(_stop_scope(backend, scope), timeout=120)
            if row['stopped']:
                await asyncio.wait_for(remove_workspace(scope), timeout=30)
                row['removed'] = True
        except (Exception, asyncio.CancelledError) as exc:
            row['errors'].append(type(exc).__name__)
        results.append(row)
    complete = all(row['stopped'] and row['removed'] for row in results)
    try:
        evidence.record(scopes, 'uncertain' if not complete else ('failed' if failure else 'cleaned'),
                        cleanup=results, failure=failure)
    except Exception:
        # Durable ledger and prior scope reservations already exist privately.
        if failure is not None:
            failure.add_note('Runtime probe metadata update failed; the private durable ledger was retained.')
            return results
        raise ProbeCleanupError('Runtime probe metadata update failed; the private durable ledger was retained.') from None
    # Keep the private audit even on success. A stop request after cleanup must
    # never race deletion of the reservations that authorized remote work.
    if not complete:
        if failure is not None:
            failure.add_note('Runtime probe cleanup was incomplete; private reconciliation evidence was retained.')
        else:
            raise ProbeCleanupError('Runtime probe cleanup is unconfirmed; private reconciliation evidence was retained.')
    return results
