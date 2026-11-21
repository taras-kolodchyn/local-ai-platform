import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('build_images', Path(__file__).resolve().parents[1] / 'build-images.py')
build_images = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_images)


class ComposeImagesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.services = {}
        for name in ('first', 'second'):
            context = self.root / name
            context.mkdir()
            (context / 'Dockerfile').write_text('FROM scratch\nCOPY src /src\n')
            (context / '.dockerignore').write_text('**\n!src/**\n**/__pycache__\n')
            (context / 'src').mkdir()
            self.services[name] = dict(image=name, build=dict(context=str(context), dockerfile='Dockerfile'))
        self.src = self.root / 'first/src'
        self.receipt = self.root / 'receipts.json'
        self.images = {}
        self.builds = []
        self.fail = False

    def reconcile(self):
        def build(cmd, check):
            self.builds.append(cmd[3:])
            if self.fail:
                raise subprocess.CalledProcessError(1, cmd)
            self.images.update({n: 'image-' + n for n in cmd[3:]})

        with patch.object(build_images, 'image_ids', side_effect=lambda s: {n: self.images[n] for n in s if n in self.images}), \
             patch.object(subprocess, 'run', side_effect=build):
            build_images.reconcile(['docker', 'compose'], self.services, self.receipt)

    def test_builds_changed_and_missing_images_and_recovers_after_failures(self):
        self.reconcile()
        self.reconcile()
        self.assertEqual(self.builds, [['first', 'second']])
        del self.images['first']
        self.images['second'] = 'replaced'
        self.reconcile()
        self.assertEqual(self.builds[-1], ['first', 'second'])
        (self.src / 'new').write_text('untracked')
        self.fail = True
        saved = self.receipt.read_bytes()
        with self.assertRaises(subprocess.CalledProcessError):
            self.reconcile()
        self.assertEqual(self.receipt.read_bytes(), saved)
        self.fail = False
        self.reconcile()
        self.assertEqual(self.builds[-2:], [['first'], ['first']])
        self.receipt.write_text('invalid json')
        self.reconcile()
        self.assertEqual(self.builds[-1], ['first', 'second'])

    def test_input_identity_and_specific_ignore_precedence(self):
        first = self.services['first']
        context = self.src.parent
        def digest():
            return build_images.fingerprint(first)

        initial = digest()
        self.assertIsNotNone(initial)
        (self.src / '__pycache__').mkdir()
        (self.src / '__pycache__/ignored').write_text('ignored')
        (context / 'README').write_text('ignored')
        self.assertEqual(digest(), initial)
        path = self.src / 'new'
        path.write_text('test')
        fingerprints = [initial, digest()]
        os.utime(path, None)
        self.assertEqual(digest(), fingerprints[-1])
        path.write_text('edit')
        fingerprints.append(digest())
        path.chmod(0o755)
        fingerprints.append(digest())
        path.rename(self.src / 'renamed')
        fingerprints.append(digest())
        (self.src / 'renamed').unlink()
        self.assertEqual(digest(), initial)
        (context / 'Dockerfile.dockerignore').write_text('**\n!README\n')
        fingerprints.append(digest())
        (context / '.dockerignore').write_text('*\n')
        self.assertEqual(digest(), fingerprints[-1])
        (context / 'Dockerfile').write_text('FROM scratch\nCOPY README /README\n')
        fingerprints.append(digest())
        self.assertEqual(len(fingerprints), len(set(fingerprints)))
        self.assertNotIn(None, fingerprints)

    def test_unsupported_builds_are_never_skipped(self):
        second = self.services['second']
        second['build']['additional_contexts'] = {'assets': '.'}
        (self.src.parent / '.dockerignore').write_text('*.log\n')
        for _ in range(2):
            self.reconcile()
            self.assertEqual(self.builds[-1], ['first', 'second'])
        self.assertEqual(json.loads(self.receipt.read_text()), {})
