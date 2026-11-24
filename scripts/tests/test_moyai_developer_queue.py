"""Verify the queue's admission order and recovery around external requests."""
from copy import deepcopy
from pathlib import Path
import runpy
from unittest.mock import Mock
import unittest


QUEUE = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'moyai-developer-queue.py'))
tick = QUEUE['tick']


class GitHub:
    repository = 'example/project'

    def __init__(self):
        self.ready = [4, 7, 9]
        self.pulls = {4}
        self.sha = 'a' * 40

    def candidates(self):
        return self.ready.copy()

    def has_pull(self, issue):
        return issue in self.pulls

    def head(self):
        return self.sha


class Controller:
    def __init__(self, events):
        self.events = events
        self.sessions = {}
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
        turn = {'id': 'turn-' + chat, 'status': 'queued', 'connections': body['connections']}
        stored['turns'].insert(0, turn)
        if self.lost_receipt:
            raise ConnectionError('The response was lost.')
        return turn


class DeveloperQueueTests(unittest.TestCase):
    def setUp(self):
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
        self.repo.pulls.add(7)
        self.assertEqual(self.check()['status'], 'pull_open')
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

    def test_developer_completion_advances_to_verifier_before_next_issue(self):
        reviewer = Mock(settings=QUEUE['ReviewSettings']('separate', 'developer-app', 'verifier-app'))
        reviewer.find.return_value = 10
        result = self.check(reviewer=reviewer)
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

    def test_held_issue_becomes_eligible_when_label_is_removed_and_reapplied(self):
        self.repo.ready = [7]
        result = self.check()
        self.controller.sessions[result['session']]['turns'][0]['status'] = 'failed'
        self.assertEqual(self.check()['status'], 'held')
        self.assertEqual(self.check()['status'], 'idle')
        self.repo.ready = []
        self.assertEqual(self.check()['status'], 'idle')
        self.repo.ready = [7]
        self.assertEqual(self.check()['session'], 'chat-2')
