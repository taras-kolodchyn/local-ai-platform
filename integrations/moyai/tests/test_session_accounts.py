"""Check the Linux ownership boundary on a disposable volume layout."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import session_accounts as accounts


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'requires disposable Linux UIDs')
class SessionAccountsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        base = Path(self.tmp)
        base.chmod(0o711)
        self.root = base / 'workspace'
        self.root.mkdir(mode=0o711)
        runtime = base / 'runtime'
        runtime.mkdir()
        (runtime / 'homes').mkdir()
        (runtime / 'passwd').write_text('')
        self.registry = base / 'registry'
        self.registry.mkdir(mode=0o700)
        self.enterContext(patch.multiple(accounts, ROOT=self.root, RUN=runtime))

    def test_legacy_checkout_and_colliding_uid_candidates(self):
        first, second = 'a' * 64, 'a' * 63 + 'b'
        primary = self.root / first
        primary.mkdir()
        os.chown(primary, accounts.CONTROL_UID, accounts.CONTROL_UID)
        (primary / 'example').write_text('original')
        accounts.account(self.registry, first, True)
        accounts.account(self.registry, second, True)
        first_uid, second_uid = [p.stat().st_gid for p in (primary, self.root / second)]
        self.assertNotEqual(first_uid, second_uid)
        self.assertEqual((primary / 'example').stat().st_uid, first_uid)
        self.assertEqual(accounts.checkout_uid(self.registry, first), first_uid)
        self.assertEqual(accounts.checkout_uid(self.registry, second), second_uid)
        # Make the file readable on its own. The root-owned parent still gates it.
        (primary / 'example').chmod(0o644)
        for uid, expected in ((first_uid, 0), (second_uid, 1)):
            result = subprocess.run(['/bin/cat', primary / 'example'], user=uid, group=uid,
                                    extra_groups=[], capture_output=True)
            self.assertEqual(result.returncode, expected)
        self.assertFalse(accounts.account(self.registry, 'c' * 64, False)['exists'])
        self.assertFalse((self.root / ('c' * 64)).exists())

    def test_external_hardlink_holds_migration_before_changing_file_owners(self):
        path = self.root / ('b' * 64)
        path.mkdir()
        os.chown(path, accounts.CONTROL_UID, accounts.CONTROL_UID)
        other = Path(self.tmp) / 'external'
        other.write_bytes(b'sentinel')
        os.link(other, path / 'linked')
        with self.assertRaisesRegex(ValueError, 'shared outside'):
            accounts.account(self.registry, path.name, True)
        self.assertEqual(other.stat().st_uid, 0)
        self.assertEqual(other.read_bytes(), b'sentinel')


if __name__ == '__main__':
    unittest.main()
