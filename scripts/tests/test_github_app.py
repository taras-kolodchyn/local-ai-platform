import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import github_app as app
import github_issue

TARGET = github_issue.GitHubTarget("fixture-owner/fixture-repo", "")

spec = importlib.util.spec_from_file_location('register_github_app', SCRIPTS/'register-github-app.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class GitHubAppTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(github_issue, 'TARGET', TARGET))
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)/'app'
        registration = {'id':12,'slug':'moyai-test','owner':{'login':TARGET.repository.split('/')[0]},
                        'pem':'simulated-key','client_secret':'unused-secret', 'webhook_secret':'unused-webhook'}
        setup.save_registration(registration, self.directory)
        self.installation = app.Installation(self.directory)

    def test_manifest_and_private_storage(self):
        manifest = setup.manifest('http://127.0.0.1:1234')
        self.assertIs(manifest['public'], False)
        self.assertIs(manifest['hook_attributes']['active'], False)
        self.assertEqual(manifest['hook_attributes']['url'], 'https://github.com/'+TARGET.repository)
        self.assertEqual(manifest['default_permissions'], app.PERMISSIONS)
        self.assertEqual(manifest['redirect_url'],'http://127.0.0.1:1234/callback')
        self.assertEqual(set(p.name for p in self.directory.iterdir()), {'app.json','private-key.pem'})
        self.assertEqual(self.installation.key.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads((self.directory/'app.json').read_text()), {'id':12,'slug':'moyai-test'})

    def test_signed_jwt_verifies_with_public_key(self):
        key = self.installation.key
        subprocess.run(['openssl','genrsa','-out',str(key),'2048'],capture_output=True,check=True)
        with patch.object(app.time,'time',return_value=10000):
            jwt = app.make_jwt('12',key)
        header, claims, signature = jwt.split('.')
        unpack = lambda v: base64.urlsafe_b64decode(v+'='*(-len(v)%4))
        self.assertEqual(json.loads(unpack(header))['alg'],'RS256')
        self.assertEqual(json.loads(unpack(claims)), {'iss':'12','iat':9940,'exp':10540})
        public = self.directory/'pub'
        sig = self.directory/'sig'
        sig.write_bytes(unpack(signature))
        subprocess.run(['openssl','pkey','-in',str(key),'-pubout','-out',str(public)],check=True,capture_output=True)
        subprocess.run(['openssl','dgst','-sha256','-verify',str(public),'-signature',str(sig)],
                       input=(header+'.'+claims).encode(),check=True,capture_output=True)
        key.chmod(0o644)
        with self.assertRaises(app.AppError):
            app.make_jwt('12',key)

    def test_scoped_exchange_cached_and_refreshed_with_no_cli_transport(self):
        installation = {'id':20,'app_id':12,'app_slug':'moyai-test','repository_selection':'selected',
                        'account':{'login':TARGET.repository.split('/')[0]}}
        responses = [installation,{'token':'test-metadata'},
                     {'total_count':1,'repositories':[{'full_name':TARGET.repository,'id':77}]},
                     {'permissions':app.PERMISSIONS,'expires_at':'2026-11-18T08:00:00Z','token':'test-installation'}]
        transport = Mock(side_effect=responses+[{'state':'open'},{'sha':'a'}]+responses+[{'state':'open'}])
        with patch.object(app,'request',transport), patch.object(app,'make_jwt',return_value='test-jwt'), \
                patch.object(app.time,'time',return_value=0), patch.object(app.subprocess,'run',side_effect=AssertionError):
            self.assertEqual(self.installation.api('issues/6'), {'state':'open'})
            self.assertEqual(self.installation.api('commits/main'), {'sha':'a'})
            self.installation.expires = 1
            self.assertEqual(self.installation.api('issues/6'), {'state':'open'})
        self.assertEqual(transport.call_args_list[3].args,('/app/installations/20/access_tokens','test-jwt',
                         {'repository_ids':[77], 'permissions':app.PERMISSIONS}))
        self.assertEqual(transport.call_args_list[4].args[1],'test-installation')

    def test_multiple_repositories_refused_before_write_token(self):
        responses = [{'id':20,'app_id':12,'app_slug':'moyai-test','repository_selection':'selected',
                      'account':{'login':TARGET.repository.split('/')[0]}}, {'token':'metadata'},
                     {'total_count':2, 'repositories':[]}]
        with patch.object(app,'request',side_effect=responses) as req, patch.object(app,'make_jwt',return_value='jwt'):
            with self.assertRaisesRegex(app.AppError, 'only'):
                self.installation.api('git/refs', {'ref':'ignored'})
            self.assertEqual(req.call_count,3)
        self.assertIsNone(self.installation.token)

    def test_reader_pins_effective_permissions_and_repository(self):
        install = {'id':20, 'app_id':12, 'app_slug':'moyai-test', 'repository_selection':'selected',
                   'account':{'id':47, 'login':TARGET.repository.split('/')[0]}, 'permissions':app.PERMISSIONS}
        selection = {'total_count':1, 'repositories':[{'id':77, 'full_name':TARGET.repository}]}
        permissions = dict.fromkeys(('contents','issues','pull_requests','metadata'), 'read')
        grant = {'permissions':permissions, 'expires_at':'2026-11-18T08:00:00Z', 'token':'fixture'}
        for effective, scoped, valid in [
            (grant, selection, True), ({**grant, 'permissions':{**permissions, 'checks':'write'}}, selection, False),
            (grant, {'total_count':1, 'repositories':[{'id':78}]}, False),
        ]:
            with self.subTest(valid=valid), patch.object(app, 'make_jwt', return_value='jwt'), patch.object(
                app, 'request', side_effect=[install, {'token':'metadata'}, selection, effective, scoped]
            ):
                if valid:
                    self.installation.refresh(reader=True)
                    self.assertEqual(self.installation.authority, {
                        'app':12, 'installation':20, 'owner':47, 'repository':77,
                        'permissions':permissions, 'installation_permissions':app.PERMISSIONS})
                else:
                    with self.assertRaises(app.AppError):
                        self.installation.refresh(reader=True)
