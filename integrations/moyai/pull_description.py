"""Format the issue queue's closing reference before native write confirmation."""
from dataclasses import replace
import re

from pydantic_ai.messages import ToolCallPart, UserPromptPart


QUEUE_ISSUE = re.compile(
    r'(?:Implement|Open the pull request for) https://github\.com/([\w-]+)/([\w.-]+)/issues/([1-9][0-9]*)\. '
)


def closing_body(body, number):
    footer = f'Closes #{number}'
    text = body.rstrip()
    if text.casefold().endswith(footer.casefold()):
        before = text[:-len(footer)]
        if not before or before[-1].isspace() or before.endswith(r'\n'):
            text = before.rstrip()
            while text.endswith(r'\n'):
                text = text[:-2].removesuffix(r'\r').rstrip()
    return f'{text}\n\n{footer}' if text else footer


def format_pull_response(ctx, request, response):
    grants = ctx.deps.grants
    if len(grants) != 1 or grants[0].kind != 'github':
        return response
    name = 'github_' + grants[0].id[:8] + '_create_pull_request'
    if not any(call.tool_name == name for call in response.tool_calls):
        return response
    # A continuation after an earlier confirmation can have no new prompt.
    prompt = ctx.prompt or next((part.content for msg in reversed(request.messages)
                                 for part in reversed(msg.parts) if isinstance(part, UserPromptPart)), None)
    target = QUEUE_ISSUE.match(prompt) if isinstance(prompt, str) else None
    if not target or f'Closes #{target[3]}.' not in prompt:
        return response
    owner, repo, number = target.groups()
    parts = []
    for part in response.parts:
        if isinstance(part, ToolCallPart) and part.tool_name == name:
            try:
                args = part.args_as_dict()
            except ValueError:
                parts.append(part)
                continue
            if ((args.get('owner'), args.get('repo'), args.get('head'), args.get('base'))
                    == (owner, repo, 'issue-' + number, 'main') and isinstance(args.get('body', ''), str)):
                part = replace(part, args={**args, 'body': closing_body(args.get('body', ''), number)})
        parts.append(part)
    return replace(response, parts=parts)
