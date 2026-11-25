"""Exercise Git pinning and installation without contacting a remote service."""
from pathlib import Path
import runpy
import tempfile
import unittest


CHECKOUT = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'moyai-checkout.py'))
git = CHECKOUT['git']
branch_bundle = CHECKOUT['branch_bundle']
install_bundle = CHECKOUT['install_bundle']
CheckoutError = CHECKOUT['CheckoutError']


class CheckoutTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / 'source'
        self.sandbox = self.root / 'sandbox'
        self.source.mkdir()
        self.sandbox.mkdir()
        git('init', '--quiet', '--initial-branch=main', directory=self.source)
        git('config', 'user.name', 'Repository Fixture', directory=self.source)
        git('config', 'user.email', 'fixture@localhost', directory=self.source)
        self.bundle = self.root / 'source.bundle'
        self.key = 'a' * 64
        self.workspace = self.sandbox / self.key

    def commit(self, content):
        (self.source / 'README.md').write_text(content)
        git('add', '--all', directory=self.source)
        git('commit', '--quiet', '-m', 'Update usage', directory=self.source)
        return git('rev-parse', 'HEAD', directory=self.source).decode().strip()

    def install(self, commit):
        with self.bundle.open('rb') as bundle:
            install_bundle(self.sandbox, self.key, commit, bundle)

    def test_bundle_pins_exact_revision_without_a_remote(self):
        self.commit('Initial version\n')
        sha = self.commit('Verified version\n')
        self.assertEqual(branch_bundle(self.source.as_uri(), 'main', self.bundle, sha), sha)
        self.commit('New remote tip\n')
        self.install(sha)
        self.assertEqual((self.workspace / 'README.md').read_text(), 'Verified version\n')
        self.assertEqual(git('rev-parse', 'HEAD', directory=self.workspace).decode().strip(), sha)
        self.assertEqual(git('log', '-5', '--format=%H', directory=self.workspace).decode().splitlines(), [sha])
        self.assertFalse(git('remote', directory=self.workspace))
        self.install(sha)
        (self.workspace / 'notes.txt').write_text('Keep this file\n')
        with self.assertRaisesRegex(CheckoutError, 'Workspace already exists'):
            self.install(sha)
        self.assertEqual((self.workspace / 'notes.txt').read_text(), 'Keep this file\n')

    def test_improper_pin_does_not_publish_a_bundle(self):
        self.commit('Usage\n')
        with self.assertRaisesRegex(CheckoutError, 'differs'):
            branch_bundle(self.source.as_uri(), 'main', self.bundle, 'a' * 40)
        self.assertFalse(self.bundle.exists())

    def test_symbolic_link_is_rejected_before_installation(self):
        (self.source / 'other').symlink_to('/workspace/elsewhere')
        sha = self.commit('Usage\n')
        with self.assertRaisesRegex(CheckoutError, 'requires regular files'):
            branch_bundle(self.source.as_uri(), 'main', self.bundle, sha)
        self.assertFalse(self.bundle.exists())
        self.assertFalse(self.workspace.exists())
