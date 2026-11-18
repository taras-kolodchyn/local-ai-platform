from hashlib import sha256
import importlib.util
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
                prs.append({'head':side,'base':{**side,'ref':'main'},'state':'open','draft':True,'number':7})
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

    def test_app_publish_reconciles_only_its_branch_and_bot_author(self):
        installation = SimpleNamespace(app_id=42, bot='moyai-test[bot]')
        job = {'number':6, 'base':self.base, 'commit':'a'*40}
        branch = 'codex/app-42/issue-6'
        side = {'ref':branch,'sha':job['commit'],'repo':{'full_name':publisher.REPO}}
        author = {'type':'User','login':'operator'}
        def api(endpoint, body=None):
            self.assertIsNone(body)
            if endpoint == 'git/matching-refs/heads/'+branch:
                return [{'ref':'refs/heads/'+branch,'object':{'sha':job['commit']}}]
            self.assertTrue(endpoint.endswith(':'+branch))
            return [{'head':side,'base':{**side,'ref':'main'},'state':'open','draft':True,'number':9,'user':author}]
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

    def test_issue_limit_does_not_enable_personal_identity_in_app_mode(self):
        installation = Mock()
        github = publisher.GitHub(6, installation=installation)
        with patch.object(publisher,'run',side_effect=AssertionError):
            github.api('issues/6')
            installation.api.assert_called_once_with('issues/6', None)
            with self.assertRaisesRegex(ValueError,'another issue'):
                github.ticket(7)

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
            github.ticket.assert_called_once()
            detail['session']['surface'] = 'automation'
            with self.assertRaisesRegex(ValueError, 'originating'):
                host.dispatch(request)

    def test_offline_compose_has_no_exchange(self):
        self.assertNotIn('github-issues', (MODULE.parents[1]/'compose.moyai.yaml').read_text())
        overlay = (MODULE.parents[1]/'compose.moyai-issues.yaml').read_text()
        self.assertNotIn('moyai-sandbox:', overlay)
        self.assertNotIn('installation-token', overlay)
