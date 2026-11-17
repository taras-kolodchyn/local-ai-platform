"""Run against the pinned real Moyai SDK during image build, without a network."""
from types import SimpleNamespace
import unittest

from pydantic_ai.workspaces import WorkspaceRef


class SessionWorkspaceTests(unittest.TestCase):
    def capability(self):
        try:
            from moyai_workspace import SessionWorkspace
        except ImportError:
            self.fail('Local per-session SSH workspace capability is missing')
        return SessionWorkspace()

    def context(self, session='session-one', authority='same-user-and-grants'):
        return SimpleNamespace(deps=SimpleNamespace(session_id=session, workspace_key=authority))

    def test_same_chat_reattaches_but_another_chat_cannot_use_its_ref(self):
        capability = self.capability()
        first = capability.get_workspace(self.context(), ref=None)
        self.assertEqual(first.ref.provider, 'ssh')
        self.assertIsNotNone(capability.get_workspace(self.context(), ref=first.ref))
        self.assertIsNone(capability.get_workspace(self.context('session-two'), ref=first.ref))
        self.assertIsNone(capability.get_workspace(self.context(authority='other-account'), ref=first.ref))

    def test_ref_cannot_select_an_external_host_or_local_filesystem(self):
        capability = self.capability()
        for ref in [WorkspaceRef(provider='local', id='/etc'),
                    WorkspaceRef(provider='ssh', id='other-host:/workspace/other')]:
            with self.subTest(ref=ref):
                self.assertIsNone(capability.get_workspace(self.context(), ref=ref))
