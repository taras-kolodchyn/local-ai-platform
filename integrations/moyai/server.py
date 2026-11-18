"""Run upstream Moyai with a local SSH workspace and local Temporal."""
import asyncio
import json
import os
from pathlib import Path
import pwd
import shutil

import uvicorn

from local_config import validate_secrets
from moyai_workspace import SessionWorkspace
from repository_backend import NativeFiles, RepositoryBackend, install_task_status
from repository_metrics import install_metrics
from inference import local_model
from relay import listen


def prepare():
    from docker_start import prepare_data_directory
    values = json.loads(Path('/run/moyai-config.json').read_text())
    validate_secrets(values)
    os.environ['WORKSPACE_PASSWORD'] = values['password']
    os.environ['MOYAI_REPOSITORY_CONTEXT_TOKEN'] = values['repository_context_token']
    os.environ['LITELLM_API_KEY'] = values['api_key']
    os.environ['HOME'] = '/home/workspace'
    user = pwd.getpwnam('workspace')
    prepare_data_directory(Path('/data'), user.pw_uid, user.pw_gid)
    private = Path('/run/moyai-ssh')
    private.mkdir(mode=0o700, exist_ok=True)
    private.chmod(0o700)
    for name in ('id_ed25519', 'known_hosts'):
        target = private / name
        shutil.copyfile('/run/ssh-seed/' + name, target)
        target.chmod(0o600)
        os.chown(target, user.pw_uid, user.pw_gid)
    os.chown(private, user.pw_uid, user.pw_gid)
    os.setgroups([])
    os.setgid(user.pw_gid)
    os.setuid(user.pw_uid)


async def main():
    from moyai.api import create_app
    from moyai.runtime import Runtime
    from moyai.settings import Settings
    from moyai.worker import make_worker

    async with await listen('127.0.0.1', 4000, 'moyai-bridge', 4000):
        settings = Settings(_env_file=None)
        settings.validate_worker()
        model, client = local_model(settings)
        async with client:
            runtime = Runtime(settings)
            workspace = SessionWorkspace(RepositoryBackend(plans=runtime.stores.plan, native_files=NativeFiles(runtime)))
            await runtime.start(models={'local-qwen': model}, workspace=workspace)
            try:
                await runtime.connect()
                async with make_worker(runtime):
                    app = create_app(runtime)
                    install_task_status(app, runtime, workspace.backend)
                    install_metrics(app)
                    await uvicorn.Server(uvicorn.Config(
                        app, host='0.0.0.0', port=8787, access_log=False, log_level='warning',
                    )).serve()
            finally:
                await runtime.close()


if __name__ == '__main__':
    prepare()
    asyncio.run(main())
