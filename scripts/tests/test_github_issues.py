from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

MODULE = Path(__file__).resolve().parents[1] / 'github-issues.py'
sys.path.insert(0, str(MODULE.parent))
spec = importlib.util.spec_from_file_location('github_issues', MODULE)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
from github_issue import ISSUES, issue_number, user_issue


class IssuePublishingTests(unittest.TestCase):
    def test_only_direct_root_web_issue_url(self):
        deps = SimpleNamespace(root_run_id='t', visibility='private', surface='web')
        ctx = SimpleNamespace(run_id='t', deps=deps, prompt=ISSUES+'6')
        self.assertEqual(user_issue(ctx), 6)
        for text in ('look at '+ctx.prompt, ctx.prompt+'?owner=else', ctx.prompt+'#comment',
                     ctx.prompt.replace('github.com', 'github.com.example.org'), ISSUES+'-1'):
            self.assertIsNone(issue_number(text))
        for key, value in [('delegation_depth', 1), ('swarm_id','swarm'), ('surface','slack'), ('visibility','shared')]:
            with patch.object(deps, key, value, create=True):
                self.assertIsNone(user_issue(ctx))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.project = self.repo / publisher.PROJECT
        (self.project/'src').mkdir(parents=True)
        (self.project/'src/lib.rs').write_text('old\n')
        (self.repo/'untouched').write_text('unrelated\n')
        git = lambda *args: publisher.git(self.repo,*args)
        git('init','-q')
        git('add','.')
        git('-c','user.email=test@localhost','-c','user.name=Test','commit','-qm','base')
        self.base = git('rev-parse','HEAD').decode().strip()
        self.patch = b'--- a/src/lib.rs\n+++ b/src/lib.rs\n@@ -1 +1 @@\n-old\n+new\n'
        def row(content):
            return {'path':'src/lib.rs','sha256':sha256(content).hexdigest(),'size':len(content)}
        self.manifest = {'files':[row(b'old\n')]}
        self.digest = sha256(publisher.canonical([row(b'new\n')])).hexdigest()

    def test_publish_reconstructs_pinned_full_tree_and_confines_paths(self):
        tree, entries = publisher.reproduce(self.repo,self.base,self.manifest,self.patch,self.digest)
        self.assertEqual(entries[0]['path'],publisher.PROJECT+'/src/lib.rs')
        self.assertEqual(publisher.git(self.repo,'show',tree+':untouched'),b'unrelated\n')
        self.assertEqual((self.project/'src/lib.rs').read_text(),'old\n')
        with self.assertRaisesRegex(ValueError,'reproduce'):
            publisher.reproduce(self.repo,self.base,self.manifest,self.patch,'bad')
        bad = self.patch.replace(b'src/lib.rs',b'../../untouched')
        with self.assertRaises((ValueError,subprocess.CalledProcessError)):
            publisher.reproduce(self.repo,self.base,self.manifest,bad,self.digest)

    def test_prepare_reconciles_existing_snapshot_with_active_embedding_generation(self):
        (self.repo / 'sources' / self.base).mkdir(parents=True)
        catalog = self.repo / '.local/repositories'
        catalog.mkdir(parents=True)
        scope = {'repository_id': 'repo_github_' + self.base, 'snapshot_id': 'a' * 64}
        old = {**scope, 'generation_id': 'gen_old', 'store_id': 'vs_old'}
        current = {**scope, 'generation_id': 'gen_current', 'store_id': 'vs_current'}
        (catalog / 'catalog.json').write_text(json.dumps({'repositories': [old]}))
        manifest = {**scope, 'git': {'head': self.base, 'dirty': False, 'subtree': publisher.PROJECT}}
        registration = Mock()
        registration.sibling.return_value.prepare_snapshot.return_value = manifest
        registration.index_prepared.return_value = current
        with (patch.object(publisher, 'ROOT', self.repo), patch.object(publisher, 'STATE', self.repo),
              patch.object(publisher.importlib.util, 'spec_from_file_location'),
              patch.object(publisher.importlib.util, 'module_from_spec', return_value=registration)):
            self.assertEqual(publisher.prepare(self.base), (manifest, current))
        registration.index_prepared.assert_called_once_with(catalog, manifest)

    def test_lost_pr_response_reconciles_without_duplicate_branch_commit_or_pr(self):
        tree, _ = publisher.reproduce(self.repo,self.base,self.manifest,self.patch,self.digest)
        head, refs, prs, calls = 'c'*40, [], [], []
        branch = 'codex/issue-6'
        side = {'ref':branch,'sha':head,'repo':{'full_name':publisher.REPO}}
        def api(path, body=None):
            calls.append(path)
            if path == 'git/trees': return {'sha':tree}
            if path == 'git/commits': return {'sha':head,'tree':{'sha':tree},'parents':[{'sha':self.base}]}
            if path.startswith('git/matching-refs'): return refs
            if path == 'git/refs':
                refs.append({'ref':'refs/heads/'+branch,'object':{'sha':head}})
                return {}
            if path.startswith('pulls?'): return prs
            if path == 'pulls':
                self.assertIs(body['draft'],True)
                prs.append({'head':side,'base':{**side,'ref':'main','sha':self.base},'state':'open','draft':True,'number':7})
                raise TimeoutError
            self.fail(path)
        job = {'base':self.base,'number':6}
        checks = {'project_tests_run':3,'oracle_tests_run':4}
        def publish():
            return publisher.publish(api,self.repo,self.manifest,job,self.patch,self.digest,checks,Mock())
        with self.assertRaises(TimeoutError): publish()
        self.assertEqual(publish(), {'number':7,'head':head})
        for operation in ('git/commits','git/refs','pulls'):
            self.assertEqual(calls.count(operation),1)
        refs[0]['object']['sha'] = 'f'*40
        with self.assertRaisesRegex(ValueError,'different head'): publish()
        refs.clear()
        prs[0]['state'] = 'closed'
        with self.assertRaises(ValueError): publish()
        self.assertEqual(calls.count('git/refs'), 1)
        self.assertEqual(refs, [])

    def test_app_publish_reconciles_only_its_branch_and_bot_author(self):
        installation = SimpleNamespace(app_id=42, bot='moyai-test[bot]')
        job = {'number':6, 'base':self.base, 'commit':'a'*40}
        branch = 'codex/app-42/issue-6'
        side = {'ref':branch,'sha':job['commit'],'repo':{'full_name':publisher.REPO}}
        author = {'type':'User','login':'operator'}
        base = {**side,'ref':'main','sha':self.base}
        def api(endpoint, body=None):
            self.assertIsNone(body)
            if endpoint == 'git/matching-refs/heads/'+branch:
                return [{'ref':'refs/heads/'+branch,'object':{'sha':job['commit']}}]
            self.assertTrue(endpoint.endswith(':'+branch))
            return [{'head':side,'base':base,'state':'open','draft':True,'number':9,'user':author}]
        def reconcile():
            return publisher.publish(api, self.repo, self.manifest, job, self.patch, self.digest, {}, Mock(),
                                     installation=installation)
        with self.assertRaisesRegex(ValueError,'author'):
            reconcile()
        author.update(type='Bot',login='other[bot]')
        with self.assertRaisesRegex(ValueError,'author'):
            reconcile()
        author['login'] = installation.bot
        self.assertEqual(reconcile(), {'number':9, 'head':job['commit'], 'author':installation.bot, 'app_id':42})
        base['sha'] = 'b'*40
        with self.assertRaisesRegex(ValueError,'verified commit'): reconcile()

    def test_issue_limit_does_not_enable_personal_identity_in_app_mode(self):
        installation = Mock()
        github = publisher.GitHub(6, installation=installation)
        with patch.object(publisher,'run',side_effect=AssertionError):
            github.api('issues/6')
            installation.api.assert_called_once_with('issues/6', None)
            with self.assertRaisesRegex(ValueError,'another issue'):
                github.ticket(7)

    def test_cancelled_turn_cannot_create_branch_or_draft_after_verification(self):
        session, turn = str(uuid.uuid4()), str(uuid.uuid4())
        request = {'number':6, 'session':session, 'turn':turn, 'action':'publish'}
        binding = {'snapshot_id':'snapshot'}
        manifest = {**self.manifest,'git':{'head':self.base, 'dirty':False, 'subtree':publisher.PROJECT}}
        snapshot = self.repo/'.local/repositories/snapshots/snapshot'
        snapshot.mkdir(parents=True)
        (snapshot/'manifest.json').write_text(json.dumps(manifest))
        ticket = {'title':'Retry','body':'Repair'}
        job = {**request, 'base':self.base,'binding':binding,'issue':ticket,'commit':'c'*40}
        (self.repo/'issue-6.json').write_text(json.dumps(job))
        detail = {'session':{'visibility':'private','surface':'web','owner_id':'actor','active_turn':turn},
                  'messages':[{'role':'user','is_self':True,'turn_id':turn,'content':ISSUES+'6'}],
                  'turns':[{'id':turn,'status':'running','actor_id':'actor'}]}
        stopped = {**detail,'session':{**detail['session'],'active_turn':None}}
        ref = {'ref':'refs/heads/codex/issue-6','object':{'sha':job['commit']}}
        for refs in ([], [ref]):
            with self.subTest(existing_ref=bool(refs)):
                def api(endpoint, body=None):
                    self.assertIsNone(body, f'Unexpected GitHub write to {endpoint}')
                    if endpoint == 'commits/main': return {'sha':self.base}
                    if endpoint.startswith('git/matching-refs/'): return refs
                    if endpoint.startswith('pulls?'): return []
                    self.fail(endpoint)
                github = Mock(operator_issue=6, installation=None)
                github.api.side_effect, github.ticket.return_value = api, ticket
                client = Mock()
                client.request.side_effect = [detail, stopped]
                host = publisher.Publisher(github, client, manifest, binding)
                with patch.object(publisher,'ROOT',self.repo), patch.object(publisher,'STATE',self.repo), \
                        patch.object(publisher,'verified_patch',return_value=(self.patch,self.digest,
                                     {'project_tests_run':1,'oracle_tests_run':4})), \
                        patch.object(publisher,'reproduce',return_value=('d'*40, [])):
                    with self.assertRaisesRegex(ValueError, 'originating private turn'):
                        host.dispatch(request)

    def test_intake_pins_only_original_operator_message_and_allows_same_chat_recovery(self):
        session_id, turn = str(uuid.uuid4()), str(uuid.uuid4())
        request = dict(action='open', number=6, session=session_id, turn=turn)
        detail = {'session':{'visibility':'private', 'surface':'web', 'owner_id':'actor', 'active_turn':turn},
                  'turns':[{'id':turn, 'status':'running', 'actor_id':'actor'}],
                  'messages':[{'role':'user', 'content':ISSUES+'6', 'turn_id':turn, 'is_self':True}]}
        github, client = Mock(operator_issue=6, installation=None), Mock()
        github.ticket.return_value = {'title':'Retry', 'body':'Repair'}
        github.api.return_value = {'sha':self.base}
        client.request.return_value = detail
        host = publisher.Publisher(github, client, {'git':{'head':self.base}}, {'repository_id':'r'})
        with patch.object(publisher, 'STATE', self.repo):
            detail['messages'][0]['source'] = 'delegation'
            with self.assertRaisesRegex(ValueError, 'direct operator'):
                host.dispatch(request)
            github.ticket.assert_not_called()
            del detail['messages'][0]['source']
            opened = host.dispatch(request)
            self.assertEqual(opened['turn'], turn)
            later = str(uuid.uuid4())
            request['turn'] = detail['messages'][0]['turn_id'] = detail['turns'][0]['id'] = later
            detail['session']['active_turn'] = later
            self.assertEqual(host.dispatch(request), opened)
            self.assertEqual(github.ticket.call_count,2)
            github.ticket.return_value = {'title':'Updated requirements', 'body':'Changed'}
            with self.assertRaisesRegex(ValueError, 'issue changed'):
                host.dispatch(request)
            detail['session']['surface'] = 'automation'
            with self.assertRaisesRegex(ValueError, 'originating'):
                host.dispatch(request)

    def test_offline_compose_has_no_exchange(self):
        self.assertNotIn('github-issues', (MODULE.parents[1]/'compose.moyai.yaml').read_text())
        overlay = (MODULE.parents[1]/'compose.moyai-issues.yaml').read_text()
        self.assertNotIn('moyai-sandbox:', overlay)
        self.assertNotIn('installation-token', overlay)
