import tempfile
import time
import unittest
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
from moyai.security import Security
from github_reader import record_path, reference, resolve, store


class ReaderRenewalTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.security = Security(Fernet.generate_key())
        self.connection = str(uuid4())
        self.identity = 'connection_' + self.connection
        self.authority = dict(app=1, installation=2, owner=3, repository=4,
                              permissions={'issues': 'read'}, installation_permissions={'issues': 'read'})
        self.marker = reference(self.authority)
        self.grant = dict(token='first', expires=time.time() + 300, authority=self.authority)
        store(self.security, self.directory, self.connection, self.grant, enroll=True)

    def resolve(self, identity=None, read_only=True):
        return resolve(self.security, self.directory, identity or self.identity, self.marker, read_only)

    def test_rotation_keeps_reference(self):
        self.assertEqual(self.resolve(), 'first')
        for token in ('second', 'third'):
            store(self.security, self.directory, self.connection, dict(self.grant, token=token), enroll=False)
            self.assertEqual(self.resolve(), token)
        self.assertNotIn('third', record_path(self.directory, self.connection).read_text())

    def test_changed_authority_rejected(self):
        for field in self.authority:
            with self.subTest(field=field), self.assertRaises(ValueError):
                changed = {**self.authority, field: 'different'}
                store(self.security, self.directory, self.connection, dict(self.grant, authority=changed), enroll=False)
        self.assertEqual(self.resolve(), 'first')

    def test_other_connection_and_write_mode_rejected(self):
        with self.assertRaises(FileNotFoundError):
            self.resolve('connection_' + str(uuid4()))
        with self.assertRaises(ValueError):
            self.resolve(read_only=False)

    def test_expiry_and_older_renewal(self):
        from unittest.mock import patch
        with self.assertRaises(ValueError):
            store(self.security, self.directory, self.connection,
                  dict(self.grant, expires=self.grant['expires'] - 1), enroll=False)
        with patch('github_reader.time.time', return_value=self.grant['expires']):
            with self.assertRaises(ValueError):
                self.resolve()
