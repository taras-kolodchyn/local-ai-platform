"""Exercise review routing, revision checks and post-merge CI reporting."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from moyai_queue_review import ReviewQueue, ReviewSettings


HEAD = 'a' * 40
MERGE = 'b' * 40


class GitHub:
    repository = 'example/project'

    def __init__(self):
        repo = {'full_name': self.repository}
        self.pull = {
            'number': 5, 'user': {'type': 'Bot', 'login': 'developer-review[bot]'}, 'draft': False,
            'base': {'ref': 'main', 'repo': repo}, 'head': {'ref': 'issue-4', 'repo': repo, 'sha': HEAD},
            'state': 'open', 'merged': False, 'html_url': 'https://github.com/example/project/pull/5',
        }
        self.metadata = {
            'headRefOid': HEAD,
            'closingIssuesReferences': [{'number': 4, 'url': 'https://github.com/example/project/issues/4'}],
            'statusCheckRollup': [{'name': 'unit', 'conclusion': 'SUCCESS', 'detailsUrl': 'unit-job'}],
        }
        self.reviews = []
        self.check_state = 'success'
        self.pr_check_time = '2026-11-24T10:05:00Z'
        self.main_check = None
        self.issue_state = 'open'

    def approve(self, sha=HEAD, author='review-verifier[bot]', submitted_at='2026-11-24T10:00:00Z'):
        self.reviews.append({
            'id': len(self.reviews) + 1, 'commit_id': sha, 'state': 'APPROVED',
            'user': {'type': 'Bot', 'login': author}, 'html_url': 'review', 'submitted_at': submitted_at,
        })

    def open_pulls(self, issue):
        return [self.pull]

    def pull_metadata(self, number):
        return self.metadata

    def api(self, route, *, pages=False):
        if route == 'pulls/5':
            return self.pull
        if route == 'pulls/5/reviews?per_page=100':
            return [self.reviews]
        if route.startswith('actions/workflows/'):
            repo = {'full_name': self.repository}
            is_push = 'event=push' in route
            if is_push and self.main_check is None:
                return {'workflow_runs': []}
            return {'workflow_runs': [{
                'id': 43 if is_push else 42, 'path': '.github/workflows/test.yml',
                'event': 'push' if is_push else 'pull_request', 'head_branch': 'main' if is_push else 'issue-4',
                'head_sha': MERGE if is_push else HEAD, 'repository': repo, 'head_repository': repo,
                'status': 'completed', 'conclusion': self.main_check if is_push else self.check_state,
                'html_url': 'ci-run',
                'run_number': 55, 'run_attempt': 2, 'pull_requests': [],
            }]}
        if route == 'actions/runs/42/attempts/2/jobs?per_page=100':
            return [{'jobs': [{'name': 'unit', 'head_sha': HEAD, 'completed_at': self.pr_check_time,
                               'conclusion': self.check_state, 'html_url': 'unit-job'}]}]
        if route == 'actions/runs/43/attempts/2/jobs?per_page=100':
            return [{'jobs': [{'name': 'unit', 'head_sha': MERGE, 'conclusion': self.main_check}]}]
        if route == 'issues/4':
            return {'state': self.issue_state}
        raise AssertionError(route)


class Controller:
    def __init__(self):
        self.sessions = {}
        self.writes = []

    def session(self, name):
        address = f'chat-{len(self.sessions) + 1}'
        self.sessions[address] = {'turns': []}
        return address

    def request(self, route, body=None):
        chat = route.split('/')[3]
        if body is None:
            return self.sessions[chat]
        self.writes.append(body)
        turn = {'id': 'turn-' + chat, 'status': 'queued', 'connections': body['connections']}
        self.sessions[chat]['turns'] = [turn]
        return turn


class QueueReviewTests(unittest.TestCase):
    def setUp(self):
        self.repo = GitHub()
        self.controller = Controller()
        self.job = {'issue': 4, 'pull': 5}
        self.review = ReviewQueue(
            self.repo, ReviewSettings('review-connection', 'developer-review', 'review-verifier'),
            lambda client, connection, tools: 1,
        )

    def tick(self):
        return self.review.tick(self.job, lambda: self.controller, lambda: None)

    def test_verifier_gets_linked_pull_and_exact_head_once(self):
        self.repo.approve(sha='c' * 40)
        self.repo.approve(author='unrelated[bot]')
        self.assertEqual(self.review.find(4), 5)
        self.assertEqual(self.tick()['status'], 'review_submitted')
        body, = self.controller.writes
        self.assertEqual(body['connections'], ['review-connection'])
        self.assertIn(HEAD, body['prompt'])
        self.assertIn('/pull/5', body['prompt'])
        self.assertIn('Use normal confirmation', body['prompt'])
        self.assertEqual(self.tick()['status'], 'review_busy')
        self.assertEqual(len(self.controller.writes), 1)

    def test_unlinked_or_foreign_pull_does_not_start_review(self):
        self.repo.metadata['closingIssuesReferences'] = []
        self.assertEqual(self.tick()['status'], 'issue_link_pending')
        self.repo.pull['head']['repo'] = {'full_name': 'different/project'}
        self.assertIsNone(self.review.find(4))
        self.assertEqual(self.tick()['status'], 'pull_mismatch')
        self.assertFalse(self.controller.writes)

    def test_approval_needs_a_pass_for_this_pr_as_well_as_its_head(self):
        self.repo.approve()
        self.repo.metadata['statusCheckRollup'] = None
        self.assertEqual(self.tick()['status'], 'pr_ci_pending')
        self.repo.metadata['statusCheckRollup'] = [{'conclusion': 'SUCCESS', 'detailsUrl': 'other-job'}]
        self.assertEqual(self.tick()['status'], 'pr_ci_pending')
        self.repo.metadata['statusCheckRollup'][0]['detailsUrl'] = 'unit-job'
        self.assertEqual(self.tick()['status'], 'ready_to_merge')
        self.assertFalse(self.controller.writes)

    def test_changed_head_cannot_reuse_review_or_start_a_parallel_chat(self):
        first = self.tick()
        self.repo.pull['head']['sha'] = self.repo.metadata['headRefOid'] = 'c' * 40
        chat = self.controller.sessions[first['review_session']]
        chat['pending'] = {'approvals': [1]}
        self.assertEqual(self.tick()['status'], 'review_stale')
        chat.pop('pending')
        chat['turns'][0]['status'] = 'completed'
        self.assertNotEqual(self.tick()['review_session'], first['review_session'])
        self.assertIn('c' * 40, self.controller.writes[-1]['prompt'])

    def test_main_green_requires_prior_verifier_approval_merge_ci_and_closed_issue(self):
        self.repo.pull.update(state='closed', merged=True, merge_commit_sha=MERGE, merged_at='2026-11-24T10:10:00Z')
        self.repo.approve(submitted_at='2026-11-24T10:20:00Z')
        self.assertEqual(self.tick()['status'], 'merged_without_verifier')
        self.repo.reviews.clear()
        self.repo.approve()
        self.assertEqual(self.tick()['status'], 'main_pending')
        self.repo.main_check = 'success'
        self.assertEqual(self.tick()['status'], 'closing_issue')
        self.repo.issue_state = 'closed'
        self.assertEqual(self.tick()['status'], 'main_green')
        self.repo.pr_check_time = '2026-11-24T10:15:00Z'
        self.assertEqual(self.tick()['status'], 'merged_pr_ci_failed')
        self.repo.pr_check_time = '2026-11-24T10:05:00Z'
        self.repo.main_check = 'failure'
        self.assertEqual(self.tick()['status'], 'main_failed')
