import unittest
from types import SimpleNamespace
from unittest.mock import patch

from moyai.connection_policy import ConnectionPolicy
from moyai.contracts import Grant, RunScope
from pydantic_ai.tools import ToolDefinition
from verifier_review import REVIEW, allows_tool


class VerifierReviewTests(unittest.TestCase):
    def setUp(self):
        self.scope = RunScope('session', 'turn', 'user', 'local')
        self.grant = Grant('verifier', 'github', 1, '', read_only=False)
        self.tool = ToolDefinition(name=REVIEW)
        self.policy = ConnectionPolicy(allowed_tools=[REVIEW], require_approval=True)

    @patch.dict('os.environ', MOYAI_VERIFIER_CONNECTION_ID='verifier')
    def test_only_explicit_confirmed_private_review(self):
        self.assertTrue(allows_tool(self.scope, self.grant, self.policy, self.tool))
        for grant in (Grant('developer', 'github', 1, '', False),
                      Grant('verifier', 'github', 1, '', True),
                      Grant('verifier', 'mcp', 1, '', False)):
            self.assertFalse(allows_tool(self.scope, grant, self.policy, self.tool))
        for policy in (ConnectionPolicy(), ConnectionPolicy(allowed_tools=[]),
                       ConnectionPolicy(allowed_tools=[REVIEW], require_approval=False),
                       ConnectionPolicy(allowed_tools=[REVIEW], product={})):
            self.assertFalse(allows_tool(self.scope, self.grant, policy, self.tool))
        for tool in (ToolDefinition(name=REVIEW, metadata={'annotations': {'readOnlyHint': True}}),
                     ToolDefinition(name='merge_pull_request')):
            self.assertFalse(allows_tool(self.scope, self.grant, self.policy, tool))
        public = SimpleNamespace(authority=SimpleNamespace(visibility='shared'))
        self.assertFalse(allows_tool(public, self.grant, self.policy, self.tool))

    @patch.dict('os.environ', MOYAI_VERIFIER_CONNECTION_ID='')
    def test_default_and_existing_tools(self):
        self.assertFalse(allows_tool(self.scope, self.grant, self.policy, self.tool))
        self.assertTrue(allows_tool(self.scope, self.grant, ConnectionPolicy(), ToolDefinition(name='issue_read')))
