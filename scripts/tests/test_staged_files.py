"""Verify the immutable inputs of a confirmed file publication."""
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

snapshot = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'integrations/moyai/staged_files.py'))['snapshot']


class StagedFilesTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temp).resolve()
        self.git('init', '-q')
        self.file = self.root / 'README.md'
        self.file.write_bytes(b'old\n')
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost', 'commit', '-qm', 'Initial')
        self.base = self.git('rev-parse', 'HEAD')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root).decode().strip()

    def record(self):
        self.git('add', '-A')
        self.git('update-ref', 'refs/workflow/tested', self.git('write-tree'))

    def test_exact_contents_including_line_endings_and_empty_file(self):
        extra = self.root / 'empty.txt'
        extra.touch()
        for data in ('text\n', 'text', 'привіт\r\n'):
            self.file.write_bytes(data.encode())
            self.record()
            self.assertEqual(snapshot(self.root, self.base), [
                {'path': 'README.md', 'content': data}, {'path': 'empty.txt', 'content': ''}])

    def test_missing_receipt_changed_index_or_dirty_checkout_cannot_publish(self):
        self.file.write_bytes(b'tested')
        with self.assertRaisesRegex(ValueError, 'stage_changes'):
            snapshot(self.root, self.base)
        self.record()
        self.file.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            snapshot(self.root, self.base)
        self.git('add', '.')
        with self.assertRaises(ValueError):
            snapshot(self.root, self.base)

    def test_receipt_for_base_does_not_hide_a_missing_git_add(self):
        self.record()
        self.file.write_bytes(b'new example\n')
        with self.assertRaisesRegex(ValueError, 'stage_changes'):
            snapshot(self.root, self.base)
        self.git('add', '--', 'README.md')
        with self.assertRaisesRegex(ValueError, 'record_tests'):
            snapshot(self.root, self.base)
        self.git('update-ref', '-d', 'refs/workflow/tested')
        with self.assertRaisesRegex(ValueError, 'record_tests'):
            snapshot(self.root, self.base)
        self.record()
        self.assertEqual(snapshot(self.root, self.base), [{'path': 'README.md', 'content': 'new example\n'}])

    def test_nothing_to_publish_deletion_binary_and_symlink(self):
        self.record()
        with self.assertRaises(ValueError):
            snapshot(self.root, self.base)
        self.file.write_bytes(b'x\0y')
        self.record()
        with self.assertRaises(ValueError):
            snapshot(self.root, self.base)
        self.file.unlink()
        self.record()
        with self.assertRaises(ValueError):
            snapshot(self.root, self.base)
        self.file.symlink_to('other')
        self.record()
        with self.assertRaises(ValueError):
            snapshot(self.root, self.base)
