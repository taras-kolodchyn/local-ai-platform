"""Per-conversation workspaces on the dedicated local SSH sandbox."""
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai_harness.ssh_workspace import SSHWorkspaceBackend

from local_config import workspace_directory

DESTINATION = 'ssh://agent@moyai-sandbox:22'
SSH_ARGS = ('-i', '/run/moyai-ssh/id_ed25519', '-o', 'IdentitiesOnly=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o',
            'UserKnownHostsFile=/run/moyai-ssh/known_hosts')


class SessionBackend(SSHWorkspaceBackend):
    def __init__(self, directory):
        super().__init__(DESTINATION, working_dir=directory, ssh_args=SSH_ARGS)
        self.directory = directory
        self.ready = False

    async def working_dir(self):
        if not self.ready:
            parent = SSHWorkspaceBackend(DESTINATION, working_dir='/workspace', ssh_args=SSH_ARGS)
            result = await parent.run(['mkdir', '-p', '--', self.directory], timeout=15)
            if result.exit_code:
                raise RuntimeError('Could not create the local conversation workspace')
            self.ready = True
        return await super().working_dir()


class SessionWorkspace(AbstractCapability):
    def get_instructions(self):
        return [
            'Local coding profile: your files and shell commands run as a nonroot user in a local Linux '
            'SSH sandbox. The conversation has its own working directory. Modal, AWS, desktop/browser '
            'control and external connected services are unavailable in this profile. Use native file '
            'and shell tools for coding and tests; use save_artifact for downloadable deliverables. '
            'Invoke tools through structured API tool calls. Never print XML tool markup or describe '
            'a tool invocation as if it had executed. Inspect the actual tool result before reporting success.'
        ]

    def get_workspace(self, ctx, *, ref):
        backend = SessionBackend(workspace_directory(ctx.deps.session_id, ctx.deps.workspace_key))
        if ref is not None and ref != backend.ref:
            return None
        return backend
