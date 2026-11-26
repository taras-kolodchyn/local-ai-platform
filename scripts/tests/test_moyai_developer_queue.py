"""Verify the queue's admission order and recovery around external requests."""
from copy import deepcopy
from pathlib import Path
import runpy
from unittest.mock import Mock, patch
import unittest


QUEUE = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'moyai-developer-queue.py'))
tick = QUEUE['tick']


class GitHub:
    repository = 'example/project'

    def __init__(self):
        self.ready = [4, 7, 9]
        self.pulls = {4}
        self.sha = 'a' * 40
        self.branches = {}
        self.commits = {}
        self.has_developer_comment = Mock(return_value=False)

    def candidates(self):
        return self.ready.copy()

    def has_pull(self, issue):
        return issue in self.pulls

    def head(self):
        return self.sha

    def issue_branch(self, issue):
        return self.branches.get(issue)

    def issue_snapshot(self, issue):
        return {'title': 'Document the default output', 'body': 'Add one example above the first existing example.'}

    def published_commit(self, issue, parent, developer_app):
        tip = self.issue_branch(issue)
        return tip if self.commits.get(tip) == (parent, developer_app) else None


class Controller:
    def __init__(self, events):
        self.events = events
        self.sessions = {}
        self.requests = []
        self.lost_receipt = False
        self.revision = 1

    def session(self, title):
        name = f'chat-{len(self.sessions) + 1}'
        self.events.append(('session', name))
        self.sessions[name] = {'turns': []}
        return name

    def request(self, path, body=None):
        if path == '/api/connections':
            return [{'id': 'developer', 'kind': 'github', 'enabled': True, 'shared': False,
                     'read_only': False, 'revision': self.revision,
                     'policy': {'require_approval': True, 'allowed_tools': list(QUEUE['REQUIRED'])}}]
        chat = path.split('/')[3]
        stored = self.sessions[chat]
        if body is None:
            return stored
        self.events.append(('submit', chat, body['request_key']))
        self.requests.append(body)
        turn_id = 'turn-' + chat + (f"-{len(stored['turns']) + 1}" if stored['turns'] else '')
        turn = {'id': turn_id, 'status': 'queued', 'connections': body['connections']}
        stored['turns'].insert(0, turn)
        if self.lost_receipt:
            raise ConnectionError('The response was lost.')
        return turn


class DeveloperQueueTests(unittest.TestCase):
    def setUp(self):
        self.index = Mock(return_value='ready')
        self.enterContext(patch.dict(tick.__globals__, publication_index=self.index))
        self.events = []
        self.repo = GitHub()
        self.controller = Controller(self.events)
        self.state = {'held': []}
        self.saved = deepcopy(self.state)

    def save(self, state):
        self.saved = deepcopy(state)

    def checkout(self, repo, branch, chat, connection, sha):
        self.events.append(('checkout', repo, branch, chat, connection, sha))

    def check(self, checkout=None, reviewer=None):
        return tick(self.repo, lambda: self.controller, checkout or self.checkout,
                    'developer', self.state, self.save, reviewer)

    def test_new_branch_is_created_remotely_before_editing(self):
        created = self.check()
        self.assertEqual(created['stage'], 'create_branch')
        self.assertEqual(self.events[1][2], 'main')
        message = self.controller.requests[0]['prompt']
        self.assertLess(message.index('issue_read'), message.index('discover create_branch'))
        self.assertIn('branch issue-7, and from_branch main', message)
        self.assertNotIn('push_files', message)
        self.assertNotIn('requested edits', message)
        turns = self.controller.sessions[created['session']]['turns']
        turns[0]['status'] = 'failed'  # GitHub may already have accepted the confirmed write.
        self.repo.branches[7] = self.repo.sha
        self.state = deepcopy(self.saved)
        self.repo.has_developer_comment.return_value = True
        reviewer = Mock(settings=QUEUE['ReviewSettings']('separate', 'developer-app', 'verifier-app'))
        edited = self.check(reviewer=reviewer)
        self.assertEqual(edited['stage'], 'edit')
        self.assertEqual(edited['session'], created['session'])
        self.assertIn(self.repo.issue_snapshot(7)['body'], self.controller.requests[1]['prompt'])
        self.repo.has_developer_comment.assert_called_once_with(7, 'developer-app')
        self.assertNotIn('start comment', self.controller.requests[1]['prompt'])
        self.assertNotIn('create_branch', self.controller.requests[1]['prompt'])
        turns[0]['status'] = 'completed'
        self.assertEqual(self.check(reviewer=reviewer)['stage'], 'publish')
        self.assertIn('push_files', self.controller.requests[2]['prompt'])

    def test_moved_remote_branch_blocks_editing(self):
        created = self.check()
        self.repo.branches[7] = 'c' * 40
        self.controller.sessions[created['session']]['turns'][0]['status'] = 'completed'
        self.assertEqual(self.check()['status'], 'branch_changed')
        self.assertEqual(len(self.controller.requests), 1)

    def test_checkout_precedes_submission_and_waiting_input_blocks_next_issue(self):
        result = self.check()
        self.assertEqual((result['status'], result['issue']), ('submitted', 7))
        self.assertEqual([event[0] for event in self.events], ['session', 'checkout', 'submit'])
        self.assertEqual(self.events[1][-1], self.repo.sha)
        self.assertEqual(self.check()['status'], 'busy')
        chat = self.controller.sessions[result['session']]
        chat['pending'] = {'approvals': [1]}
        chat['turns'][0]['status'] = 'waiting'
        self.assertEqual(self.check()['status'], 'waiting')
        self.assertEqual(len(self.events), 3)
        chat.pop('pending')
        chat['turns'][0]['status'] = 'completed'
        self.assertEqual(self.check()['status'], 'branch_missing')
        self.assertEqual(self.check()['issue'], 9)

    def test_restarts_without_duplicating_an_acknowledged_message(self):
        self.controller.lost_receipt = True
        with self.assertRaises(ConnectionError):
            self.check()
        self.state = deepcopy(self.saved)  # Reload the last durable state.
        self.assertEqual(self.check()['turn'], 'turn-chat-1')
        self.assertEqual([event[0] for event in self.events], ['session', 'checkout', 'submit'])

    def test_removed_label_prevents_handoff_after_checkout(self):
        def checkout(*args):
            self.checkout(*args)
            self.repo.ready.remove(7)

        self.assertEqual(self.check(checkout)['status'], 'skipped')
        self.assertEqual([event[0] for event in self.events], ['session', 'checkout'])

    def test_new_base_uses_a_new_checkout_before_admission(self):
        def checkout(*args):
            self.checkout(*args)
            self.repo.sha = 'b' * 40

        self.assertEqual(self.check(checkout)['status'], 'base_moved')
        result = self.check()
        self.assertEqual((result['session'], result['sha']), ('chat-2', 'b' * 40))
        self.assertEqual([event[0] for event in self.events], ['session', 'checkout', 'session', 'checkout', 'submit'])

    def test_connection_revision_change_rebuilds_the_workspace(self):
        def checkout(*args):
            self.checkout(*args)
            self.controller.revision = 2

        self.assertEqual(self.check(checkout)['status'], 'connection_changed')
        result = self.check()
        self.assertEqual(result['session'], 'chat-2')
        self.assertEqual([event[0] for event in self.events], ['session', 'checkout', 'session', 'checkout', 'submit'])

    def test_legacy_inflight_turn_advances_to_verifier_before_next_issue(self):
        reviewer = Mock(settings=QUEUE['ReviewSettings']('separate', 'developer-app', 'verifier-app'))
        reviewer.find.return_value = 10
        result = self.check(reviewer=reviewer)
        self.state['active'].pop('stage')  # Jobs submitted by the previous queue version.
        self.controller.sessions[result['session']]['turns'][0]['status'] = 'completed'
        self.assertEqual(self.check(reviewer=reviewer), {'status': 'review_ready', 'issue': 7, 'pull': 10})
        reviewer.find.assert_called_once_with(7)
        self.state = deepcopy(self.saved)
        reviewer.tick.return_value = {'status': 'ready_to_merge'}
        self.assertEqual(self.check(reviewer=reviewer)['status'], 'ready_to_merge')
        self.assertEqual(len(self.events), 3)
        reviewer.tick.return_value = {'status': 'main_green'}
        self.assertEqual(self.check(reviewer=reviewer)['status'], 'main_green')
        self.assertEqual(self.check(reviewer=reviewer)['issue'], 9)

    def test_existing_branch_is_edited_published_and_then_opened_for_review(self):
        branch_tip = 'b' * 40
        published = 'c' * 40
        self.repo.branches[7] = branch_tip
        reviewer = Mock(settings=QUEUE['ReviewSettings']('separate', 'developer-app', 'verifier-app'))
        reviewer.find.return_value = 10

        edited = self.check(reviewer=reviewer)
        self.assertEqual(self.events[1][2:], ('issue-7', edited['session'], 'developer', branch_tip))
        self.assertIn('issue_read', self.controller.requests[0]['prompt'])
        self.assertNotIn('push_files', self.controller.requests[0]['prompt'])
        self.assertIn('The queue verified that issue #7 is open', self.controller.requests[0]['prompt'])
        self.assertIn(self.repo.issue_snapshot(7)['body'], self.controller.requests[0]['prompt'])
        self.assertNotIn('Proceed only if', self.controller.requests[0]['prompt'])
        turns = self.controller.sessions[edited['session']]['turns']
        turns[0]['status'] = 'completed'

        self.assertEqual(self.check(reviewer=reviewer)['stage'], 'publish')
        self.assertIn('push_files', self.controller.requests[1]['prompt'])
        self.assertIn(branch_tip, self.controller.requests[1]['prompt'])
        self.assertNotIn('create_pull_request', self.controller.requests[1]['prompt'])
        turns[0]['status'] = 'resumed'
        turns.insert(0, {'id': 'answer', 'status': 'completed', 'connections': ['developer']})
        self.repo.branches[7] = published
        self.repo.commits[published] = (branch_tip, 'developer-app')

        self.assertEqual(self.check(reviewer=reviewer)['stage'], 'open_pull')
        self.assertIn('create_pull_request', self.controller.requests[2]['prompt'])
        self.assertIn(published, self.controller.requests[2]['prompt'])
        self.assertIn('Closes #7', self.controller.requests[2]['prompt'])
        reviewer.find.assert_not_called()
        turns[0]['status'] = 'completed'
        self.assertEqual(self.check(reviewer=reviewer)['status'], 'review_ready')
        self.assertEqual(len({body['request_key'] for body in self.controller.requests}), 3)

    def test_missing_git_add_repairs_the_same_checkout_before_publication(self):
        self.repo.branches[7] = self.repo.sha
        edit = self.check()
        turns = self.controller.sessions[edit['session']]['turns']
        turns[0]['status'] = 'completed'
        self.index.side_effect = ['stage_changes', 'ready']
        self.controller.lost_receipt = True
        with self.assertRaises(ConnectionError):
            self.check()
        self.controller.lost_receipt = False
        self.state = deepcopy(self.saved)
        repair = self.check()
        self.assertEqual((repair['stage'], repair['session']), ('repair_index', edit['session']))
        self.assertEqual(len(self.controller.requests), 2)
        prompt = self.controller.requests[-1]['prompt']
        self.assertIn('git add --', prompt)
        self.assertIn(self.repo.issue_snapshot(7)['body'], prompt)
        turns[0]['status'] = 'completed'
        self.assertEqual(self.check()['stage'], 'publish')
        self.assertEqual(len([e for e in self.events if e[0] == 'checkout']), 1)

    def test_unverified_workspace_is_rejected_before_reading_its_index(self):
        job = {'session': 'chat-1', 'sha': self.repo.sha}
        probe = QUEUE['publication_index']
        session = {'id': 'chat-2', 'visibility': 'private', 'surface': 'web', 'workspace': {}}
        with patch('subprocess.check_output') as command:
            with self.assertRaises(QUEUE['QueueError']):
                probe(session, job)
            session['id'] = job['session']
            session['workspace'] = {'provider': 'ssh', 'id': '/workspace/../other'}
            with self.assertRaises(QUEUE['QueueError']):
                probe(session, job)
            command.assert_not_called()

    def test_stale_test_receipt_has_bounded_repair(self):
        self.repo.branches[7] = self.repo.sha
        edit = self.check()
        self.index.return_value = 'record_tests'
        turns = self.controller.sessions[edit['session']]['turns']
        for _ in range(2):
            turns[0]['status'] = 'completed'
            self.assertEqual(self.check()['stage'], 'repair_index')
            self.assertIn('test receipt is missing or stale', self.controller.requests[-1]['prompt'])
        turns[0]['status'] = 'completed'
        self.assertEqual(self.check()['status'], 'index_not_ready')
        self.assertEqual(self.state, {'held': [7]})

    def test_existing_branch_move_before_publication_holds_the_issue(self):
        self.repo.branches[7] = self.repo.sha
        result = self.check()
        self.controller.sessions[result['session']]['turns'][0]['status'] = 'completed'
        self.repo.branches[7] = 'c' * 40
        self.assertEqual(self.check()['status'], 'branch_changed')
        self.assertEqual(self.state['held'], [7])
        self.assertEqual(len(self.controller.requests), 1)

    def test_changed_issue_eligibility_blocks_staged_edit(self):
        self.repo.branches[7] = self.repo.sha
        self.repo.issue_snapshot = Mock(return_value=None)
        self.assertEqual(self.check()['status'], 'skipped')
        self.assertEqual(self.controller.requests, [])

    def test_missing_commit_blocks_opening_the_pull_request(self):
        self.repo.branches[7] = self.repo.sha
        result = self.check()
        turns = self.controller.sessions[result['session']]['turns']
        turns[0]['status'] = 'completed'
        self.check()
        turns[0]['status'] = 'completed'
        self.assertEqual(self.check()['status'], 'publication_missing')
        self.assertEqual(len(self.controller.requests), 2)

    def test_published_commit_survives_a_failed_turn_after_the_write(self):
        self.repo.branches[7] = self.repo.sha
        result = self.check()
        turns = self.controller.sessions[result['session']]['turns']
        turns[0]['status'] = 'completed'
        self.check()
        turns[0]['status'] = 'failed'
        published = 'b' * 40
        self.repo.branches[7] = published
        self.repo.commits[published] = (self.repo.sha, None)
        self.assertEqual(self.check()['stage'], 'open_pull')

    def test_lost_publication_receipt_does_not_resubmit_or_advance(self):
        self.repo.branches[7] = self.repo.sha
        result = self.check()
        turns = self.controller.sessions[result['session']]['turns']
        turns[0]['status'] = 'completed'
        self.controller.lost_receipt = True
        with self.assertRaises(ConnectionError):
            self.check()
        self.state = deepcopy(self.saved)
        self.assertEqual(self.check()['stage'], 'publish')
        self.assertEqual(self.state['active']['turn'], turns[0]['id'])
        self.assertEqual(len(self.controller.requests), 2)

    def test_included_guidance_does_not_hide_the_completed_turn(self):
        result = self.check()
        chat = self.controller.sessions[result['session']]
        chat['turns'][0]['status'] = 'completed'
        chat['turns'].insert(0, {'id': 'guidance', 'status': 'included'})
        self.repo.branches[7] = self.repo.sha
        self.assertEqual(self.check()['stage'], 'edit')
        self.assertIn('start comment', self.controller.requests[1]['prompt'])

    def test_held_issue_becomes_eligible_when_label_is_removed_and_reapplied(self):
        self.repo.ready = [7]
        result = self.check()
        self.controller.sessions[result['session']]['turns'][0]['status'] = 'failed'
        self.assertEqual(self.check()['status'], 'branch_missing')
        self.assertEqual(self.check()['status'], 'idle')
        self.repo.ready = []
        self.assertEqual(self.check()['status'], 'idle')
        self.repo.ready = [7]
        self.assertEqual(self.check()['session'], 'chat-2')
