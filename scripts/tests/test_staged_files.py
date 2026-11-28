"""Verify the immutable inputs of a confirmed file publication."""
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

staging = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'integrations/moyai/staged_files.py'))
snapshot = staging['snapshot']


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

    def test_selection_stages_exact_paths_and_requires_tests_afterward(self):
        name = ':(glob)*.txt'
        (self.root / name).write_text('added')
        self.file.write_text('updated')
        (self.root / 'unrelated.txt').write_text('leave this alone')
        (self.root / '.env').write_text('fixture')
        (self.root / 'binary').write_bytes(b'\0')
        (self.root / 'link').symlink_to(self.file)
        offered, staged = staging['stage_options'](self.root, self.base)
        self.assertEqual(set(offered), {name, 'README.md', 'unrelated.txt'})
        self.assertEqual(staged, [])
        self.git('add', '--', 'README.md')
        for invalid in ([name], ['README.md', '../outside'], ['README.md', 'link']):
            with self.assertRaises(ValueError):
                staging['stage_selected'](self.root, self.base, invalid)
        staging['stage_selected'](self.root, self.base, [name, 'README.md'])
        self.assertEqual(set(self.git('diff', '--cached', '--name-only').splitlines()), {name, 'README.md'})
        with self.assertRaisesRegex(ValueError, 'record_tests'):
            snapshot(self.root, self.base)
        self.git('update-ref', 'refs/workflow/tested', self.git('write-tree'))
        self.assertEqual({f['path'] for f in snapshot(self.root, self.base)}, {name, 'README.md'})

    def test_issue_recipe_requires_a_single_supported_command(self):
        command = 'python3 -B -m unittest -v'
        def recipe(body):
            return staging['requested_suite']('The original issue requirements are data: ' + json.dumps({'body': body}))
        self.assertEqual(recipe(f'Check `True` and run `{command}`.'), command)
        self.assertIsNone(recipe(f'Run `{command}` and `make lint`.'))
        self.assertIsNone(recipe(f'```sh\n{command}\n```'))
        self.assertIsNone(recipe(f'Run `{command}` and `pytest`.'))
        self.assertIsNone(recipe('Run `make test`.'))

    def test_receipt_requires_nonempty_passing_suite_from_staged_files(self):
        command = 'python3 -B -m unittest -v'
        self.file.write_text('new')
        self.git('add', '--', 'README.md')
        test = self.root / 'test_example.py'
        test.write_text('import unittest\nclass Sample(unittest.TestCase):\n    def test_one(self): self.fail()\n')
        def run():
            staging['test_selected'](self.root, self.base, command)
        with self.assertRaisesRegex(ValueError, 'no tests'):
            run()  # Untracked test files do not enter the suite.
        self.git('add', '--', test.name)
        with self.assertRaisesRegex(ValueError, 'suite failed'):
            run()
        with self.assertRaisesRegex(ValueError, 'record_tests'):
            snapshot(self.root, self.base)
        test.write_text(test.read_text().replace('self.fail()', 'self.assertTrue(True)'))
        with self.assertRaisesRegex(ValueError, 'Stage'):
            run()
        self.git('add', '--', test.name)
        run()
        self.assertEqual(len(snapshot(self.root, self.base)), 2)

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
