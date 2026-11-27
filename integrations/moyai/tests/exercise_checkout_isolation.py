"""Exercise two disposable Developer workspaces over the deployed SSH transport."""
import asyncio
from pathlib import PurePosixPath
from types import SimpleNamespace
from uuid import uuid4

from pydantic_ai_harness.ssh_workspace import SSHWorkspaceBackend
from moyai_workspace import DESTINATION, SSH_ARGS, SessionWorkspace


async def exercise():
    capability = SessionWorkspace()
    contexts = [SimpleNamespace(deps=SimpleNamespace(session_id=str(uuid4()), workspace_key='checkout-probe'))
                for _ in range(2)]
    work = [capability.get_workspace(ctx, ref=None) for ctx in contexts]
    paths = [PurePosixPath(await ws.working_dir()) for ws in work]
    control = SSHWorkspaceBackend(DESTINATION, working_dir='/workspace', ssh_args=SSH_ARGS)
    try:
        identities = []
        for ws, path in zip(work, paths):
            result = await ws.run(['id', '-u'])
            identities.append(int(result.stdout))
            assert result.exit_code == 0
            await ws.write_bytes(str(path / 'marker'), path.name.encode())
        assert min(identities) > 200000 and len(set(identities)) == 2
        for i, (ws, own) in enumerate(zip(work, paths)):
            peer = paths[1 - i]
            assert await ws.read_bytes(str(own / 'marker')) == own.name.encode()
            assert capability.get_workspace(contexts[i], ref=work[1 - i].ref) is None
            for operation in (['ls', '/workspace'], ['ls', str(peer)], ['cat', str(peer / 'marker')],
                              ['touch', str(peer / 'forbidden')]):
                result = await ws.run(operation)
                assert result.exit_code != 0 and 'Permission denied' in result.stderr
            reattached = capability.get_workspace(contexts[i], ref=ws.ref)
            assert await reattached.read_bytes(str(own / 'marker')) == own.name.encode()
        print('Two separate UIDs. Own files persisted. Parent listing and peer reads and writes were denied.')
    finally:
        for ws, path in zip(work, paths):
            cleared = await ws.run(['rm', '-f', '--', 'marker', 'forbidden'])
            if cleared.exit_code or (await control.run(['rmdir', '--', str(path)])).exit_code:
                raise RuntimeError('Could not remove the disposable checkout.')


if __name__ == '__main__':
    asyncio.run(exercise())
