"""Operator-only live transport/failure probes; never a substitute for model acceptance."""
import asyncio
import json
import os
from pathlib import Path
import pwd
import sys
import uuid

from pydantic_ai_harness.ssh_workspace import SSHWorkspaceBackend
from local_config import workspace_directory
from moyai_workspace import DESTINATION, SSH_ARGS
import repository_backend as implementation
from repository_backend import RepositoryBackend
from repository_ledger import Ledger, RepositoryError, authentic_receipt
from repository_probe_cleanup import ProbeEvidence, finish_probes


async def exercise(binding):
    scopes, outcomes = [], {}
    evidence = ProbeEvidence()
    original_timeout = implementation.CHECK_TIMEOUT
    backend = RepositoryBackend(Ledger(evidence.ledger_path))
    async def opened():
        scope = ('runtime-probe-'+uuid.uuid4().hex,uuid.uuid4().hex)
        scopes.append(scope)
        evidence.record(scopes, 'running')
        await backend.open(scope, binding['repository_id'], expected_binding=binding)
        return scope

    try:
        scope = await opened()
        found = await backend.search(scope,'documented maximum retry delay 30 seconds',4)
        assert found['count'] > 0
        try:
            await backend.search((scope[0], 'foreign-authority'),'retry delay',2)
        except RepositoryError:
            outcomes['foreign_controller_authority'] = 'denied'
        else:
            raise AssertionError('Foreign controller authority reached source context')

        # Drop one real SSH reply after completion, then reconcile from a fresh adapter.
        guest = backend.guest
        async def lost_reply(scope,op,**fields):
            if op == 'check':
                before = await guest(scope,'status',operation_id=fields['operation_id'])
                assert before['state'] == 'unknown'
                receipt = await guest(scope,op,**fields)
                assert receipt['state'] == 'failed'
                raise TimeoutError('Injected loss of the completed transport response')
            return await guest(scope,op,**fields)
        backend.guest = lost_reply
        try:
            await backend.check(scope,'lost-reply')
        except TimeoutError:
            pass
        else:
            raise AssertionError('Response-loss injection did not execute')
        resumed = RepositoryBackend(Ledger(evidence.ledger_path))
        settled = await resumed.status(scope)
        assert settled['phase'] == 'analyzing' and settled['baseline_failed'] is True
        state = resumed.ledger.status(*scope)
        assert len(state['receipts']) == 1 and authentic_receipt(
            state['receipts'][0],resumed.ledger.check_key(*scope,'lost-reply'))
        outcomes['lost_response_fresh_process_reconciliation'] = 'passed'
        outcomes['pre_dispatch_status_did_not_preempt_check'] = 'passed'

        # A real pre-launch failed check must be settled and allow a subsequent repair.
        resumed.ledger.begin(*scope,[{'content':'Acceptance: retry delays never exceed 30,000 ms'}])
        workspace = SSHWorkspaceBackend(DESTINATION,working_dir=workspace_directory(*scope),ssh_args=SSH_ARGS)
        library = resumed.integration_import(binding)
        assert library, 'The selected profile must expose the pinned Rust library'
        regression = library['code'] + '\n#[test] fn retry_ceiling(){assert_eq!(bounded_backoff_ms(10),30000);}\n'
        code = """from pathlib import Path
import sys
target = Path('repository/tests/early.rs')
target.parent.mkdir(exist_ok=True)
target.write_text(sys.argv[1])
"""
        changed = await workspace.run(['python3','-I','-c',code,regression],timeout=15)
        assert changed.exit_code == 0
        failed = await resumed.check(scope,'incomplete-implementation')
        state = resumed.ledger.status(*scope)
        assert failed['state'] == 'failed' and state['phase'] == 'implementing' and state['pending'] is None
        assert authentic_receipt(state['receipts'][-1],resumed.ledger.check_key(*scope,'incomplete-implementation'))
        outcomes['signed_ordinary_failure_remains_repairable'] = 'passed'

        timeout_scope = await opened()
        implementation.CHECK_TIMEOUT = 1
        timed = await backend.__class__(backend.ledger).check(timeout_scope,'real-timeout')
        timed_state = backend.ledger.status(*timeout_scope)
        assert timed['state'] == 'timeout' and timed_state['phase'] == 'timed_out'
        assert timed_state['receipts'][-1]['processes_stopped'] is True
        outcomes['real_cargo_timeout'] = 'passed'
        implementation.CHECK_TIMEOUT = 240

        cancel_scope = await opened()
        active = asyncio.create_task(resumed.check(cancel_scope,'real-cancel'))
        try:
            for _ in range(100):
                state = resumed.ledger.status(*cancel_scope)
                if state['pending']:
                    observed = await resumed.guest(cancel_scope,'status',operation_id='real-cancel')
                    if observed['state'] == 'running':
                        break
                await asyncio.sleep(.1)
            else:
                raise AssertionError('Real check never became observable')
            active.cancel()
            try:
                await active
            except asyncio.CancelledError:
                pass
            state = resumed.ledger.status(*cancel_scope)
            assert state['phase'] == 'cancelled' and state['pending'] is None
            assert state['receipts'][-1]['processes_stopped'] is True
            outcomes['real_check_cancellation'] = 'passed'
        finally:
            if not active.done():
                active.cancel()
                try: await active
                except (Exception,asyncio.CancelledError): pass
    finally:
        failure = sys.exc_info()[1]
        implementation.CHECK_TIMEOUT = original_timeout
        async def remove_workspace(scope):
            # Called only after trusted ledger reconciliation confirms stopped work.
            workspace = SSHWorkspaceBackend(DESTINATION,working_dir='/workspace',ssh_args=SSH_ARGS)
            removed = await workspace.run(['rm','-rf','--',workspace_directory(*scope)],timeout=20)
            if removed.exit_code:
                raise RuntimeError('Could not clean up disposable runtime probe')
        try:
            cleanup_backend = RepositoryBackend(Ledger(evidence.ledger_path))
            cleanup = asyncio.create_task(finish_probes(cleanup_backend, scopes, remove_workspace,
                                                        evidence, failure=failure))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # A second stop request cannot delete reconciliation authority.
                await cleanup
                raise
        except BaseException:
            if failure is None:
                raise
            failure.add_note('Runtime probe cleanup failed; its private durable ledger was retained.')
    print(json.dumps(outcomes))


if __name__ == '__main__':
    values = json.loads(Path('/run/moyai-config.json').read_text())
    os.environ['MOYAI_REPOSITORY_CONTEXT_TOKEN'] = values['repository_context_token']
    if os.getuid() == 0:
        user = pwd.getpwnam('workspace')
        os.setgroups([]); os.setgid(user.pw_gid); os.setuid(user.pw_uid)
    asyncio.run(exercise(json.loads(sys.argv[1])))
