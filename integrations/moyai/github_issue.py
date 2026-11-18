"""Small, credential-free exchange between the Moyai controller and the opt-in host publisher."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

REPO = 'taras-kolodchyn/local-ai-platform'
ISSUES = f'https://github.com/{REPO}/issues/'
PULLS = f'https://github.com/{REPO}/pull/'
EXCHANGE = Path('/run/moyai-github-issues')


def issue_number(prompt):
    match = re.fullmatch(re.escape(ISSUES) + r'([1-9][0-9]{0,9})/?', prompt.strip()) if isinstance(prompt, str) else None
    return int(match[1]) if match else None


def issue_scope(number, turn, issue, binding):
    scope = {'number':number, 'turn':turn}
    text = (issue.get('title','') + '\n' + issue.get('body','')).lower()
    if (binding.get('verification_profile') == 'rust-retry-tests' and 'interleav' in text
            and ('u32::max' in text or re.search(r'\bextreme (?:attempt|input)s?\b', text))):
        scope['retry_contract'] = 'interleaved-extremes'
    return scope


def user_issue(ctx):
    deps = ctx.deps
    if (ctx.run_id == deps.root_run_id and getattr(deps, 'visibility', None) == 'private'
            and getattr(deps, 'surface', None) == 'web' and not getattr(deps, 'swarm_id', None)
            and not getattr(deps, 'delegation_depth', 0) and not getattr(deps, 'delegation_generated', False)):
        return issue_number(getattr(ctx, 'prompt', None))
    return None


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode='w') as stream:
        name = stream.name
        try:
            json.dump(value, stream)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        except BaseException:
            os.unlink(name)
            raise
    os.replace(name, path)


async def exchange(action, number, session, turn, *, directory=EXCHANGE):
    if not directory.is_dir():
        raise ValueError('GitHub issue mode is offline. Start make github-issues on the Mac.')
    request = dict(action=action, number=number, session=session, turn=turn)
    key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    reply = directory / 'replies' / (key + '.json')
    # A stale request from a previous worker run cannot authorize a later write.
    try:
        lease = json.loads((directory / 'lease.json').read_text())
        if time.time() - lease['updated'] > 15:
            raise ValueError('The local GitHub issue publisher is offline.')
    except FileNotFoundError as exc:
        raise ValueError('The local GitHub issue publisher is offline.') from exc
    request['lease'] = lease['id']
    request_path = directory / 'requests' / (key + '.json')
    atomic_json(request_path, request)
    for _ in range(180):
        try:
            response = json.loads(reply.read_text())
        except FileNotFoundError:
            pass
        else:
            if response.get('lease') == lease['id']:
                if 'error' in response:
                    raise ValueError(response['error'])
                return response['result']
        await asyncio.sleep(0.25)
    raise ValueError('The GitHub publisher did not confirm the operation. Resend the issue URL to reconcile.')
