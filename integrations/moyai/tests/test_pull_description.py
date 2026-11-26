"""Verify the exact pull request body presented for confirmation."""
import json
from types import SimpleNamespace
import unittest

from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from pull_description import closing_body, format_pull_response


class PullDescriptionTests(unittest.TestCase):
    def test_footer_is_an_idempotent_markdown_paragraph(self):
        summary = r'Document the literal \n sequence.'
        expected = summary + '\n\nCloses #14'
        for suffix in ('', r'\n\nCloses #14', r'\r\n\r\nCloses #14', '\n\nCloses #14',
                       '\r\ncloses #14\n', ' Closes #14'):
            self.assertEqual(closing_body(summary + suffix, '14'), expected)
        self.assertEqual(closing_body('Closes #14', '14'), 'Closes #14')

    def test_both_queue_paths_format_only_the_intended_write_before_confirmation(self):
        name = 'github_12345678_create_pull_request'
        args = dict(owner='example', repo='project', head='issue-14', base='main', title='Usage',
                    body=r'Document usage.\n\nCloses #14')
        call = ToolCallPart(name, json.dumps(args), tool_call_id='write')
        unrelated = ToolCallPart('search_tools', {'queries': ['read']})
        response = ModelResponse(parts=[unrelated, call])
        ctx = SimpleNamespace(deps=SimpleNamespace(grants=[SimpleNamespace(kind='github', id='12345678-test')]))
        for prefix in ('Implement', 'Open the pull request for'):
            prompt = f'{prefix} https://github.com/example/project/issues/14. Open a PR with Closes #14.'
            request = SimpleNamespace(messages=[ModelRequest(parts=[UserPromptPart(prompt)])])
            for ctx.prompt in (prompt, None):
                result = format_pull_response(ctx, request, response)
                self.assertIs(result.parts[0], unrelated)
                self.assertEqual(result.parts[1].tool_call_id, 'write')
                self.assertEqual(result.parts[1].args_as_dict(), {**args, 'body': 'Document usage.\n\nCloses #14'})
                self.assertEqual(call.args_as_dict(), args)

        for key, value in [('head', 'issue-15'), ('owner', 'other'), ('repo', 'other'), ('base', 'release')]:
            wrong = ToolCallPart(name, {**args, key: value})
            self.assertIs(format_pull_response(ctx, request, ModelResponse(parts=[wrong])).parts[0], wrong)
        ctx.prompt = 'Explain GitHub closing references.'
        self.assertIs(format_pull_response(ctx, request, response), response)
