import unittest

from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from observe_pytest_receipt import pytest_receipt


class PytestReceiptTests(unittest.TestCase):
    def messages(self, result, *, turn='turn', outcome='success', command='python -m pytest -q'):
        return [
            ModelResponse(parts=[ToolCallPart('shell', {'command': command}, tool_call_id='action')], run_id=turn),
            ModelRequest(parts=[ToolReturnPart('shell', result, tool_call_id='action', outcome=outcome)], run_id=turn),
        ]

    def test_only_the_matching_completed_native_pytest_action_is_accepted(self):
        output = '3 passed in 0.01s\n{"exit_code":0}'
        messages = self.messages(output)
        self.assertEqual(pytest_receipt(messages, 'turn', {'action'}), 'action')
        for history, turn, actions in (
            (self.messages('[tool result cleared]'), 'turn', {'action'}),
            (self.messages(output, outcome='failed'), 'turn', {'action'}),
            (self.messages(output, command='echo pytest'), 'turn', {'action'}),
            (self.messages('3 passed\n{"exit_code":1}'), 'turn', {'action'}),
            (messages, 'other-turn', {'action'}),
            (messages, 'turn', {'other-action'}),
        ):
            with self.subTest(turn=turn, actions=actions):
                self.assertIsNone(pytest_receipt(history, turn, actions))
