"""Small, credential-free exchange between the Moyai controller and the opt-in host publisher."""
import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import NamedTuple
import uuid

EXCHANGE = Path('/run/moyai-github-issues')
SOCKET = Path('/run/moyai-publisher/issue.sock')


class GitHubTarget(NamedTuple):
    repository: str
    project: str

    @property
    def identity(self):
        return hashlib.sha256((self.repository + '\0' + self.project).encode()).hexdigest()

    def url(self, path):
        return f'https://github.com/{self.repository}/{path}'


def parse_target(repository, project):
    if not repository and not project:
        return None
    if (not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9._-]{1,100}', repository)
            or repository.split('/')[1] in {'.', '..'}):
        raise ValueError('Set MOYAI_GITHUB_REPOSITORY to the canonical GitHub owner/name.')
    if project and (any(not part or part.startswith('.') for part in project.split('/'))
                    or any(ord(c) < 32 or ord(c) == 127 or c in '\\:*?[]' for c in project)):
        raise ValueError('MOYAI_GITHUB_PROJECT_PATH must be a safe relative directory, or empty for the root.')
    return GitHubTarget(repository, project)


try:
    TARGET = parse_target(os.getenv('MOYAI_GITHUB_REPOSITORY', ''), os.getenv('MOYAI_GITHUB_PROJECT_PATH', ''))
    TARGET_ERROR = ''
except ValueError as error:
    # Invalid optional publisher settings must leave ordinary offline chats available.
    TARGET = None
    TARGET_ERROR = str(error)


def selected_target():
    if TARGET is None:
        raise ValueError(TARGET_ERROR or 'Set MOYAI_GITHUB_REPOSITORY to the canonical GitHub owner/name.')
    return TARGET


def matches_target(ticket):
    return TARGET is not None and (ticket or {}).get('target') == TARGET.identity


def issue_number(prompt):
    if TARGET is None or not isinstance(prompt, str):
        return None
    match = re.fullmatch(re.escape(TARGET.url('issues/')) + r'([1-9][0-9]{0,9})/?', prompt.strip())
    return int(match[1]) if match else None


def requested_issue_url(ctx):
    """Parse the user's direct URL without consulting mutable publisher configuration."""
    prompt = getattr(ctx, 'prompt', None)
    if private_issue_root(ctx) and isinstance(prompt, str):
        match = re.fullmatch(r'(https://github\.com/[A-Za-z0-9_-]+/[A-Za-z0-9._-]+/issues/[1-9][0-9]{0,9})/?', prompt.strip())
        if match:
            return match[1]
    return None


def issue_scope(number, turn, issue, binding):
    scope = {'number':number, 'turn':turn, 'target':selected_target().identity}
    text = (issue.get('title','') + '\n' + issue.get('body','')).lower()
    if (binding.get('verification_profile') == 'rust-retry-tests' and 'interleav' in text
            and ('u32::max' in text or re.search(r'\bextreme (?:attempt|input)s?\b', text))):
        separate_cap = re.search(r'\b(?:capped (?:attempt|input)|ordinary cap|non-maximum capped)', text)
        both_small = re.search(r'\b0\s*(?:,|and|&)\s*2\b', text)
        scope['retry_contract'] = 'interleaved-capped-and-extreme' if separate_cap and both_small else 'interleaved-extremes'
    if (binding.get('verification_profile') == 'rust-retry-tests' and 'caller note' in text
            and 'migration' in text and 'forwards' in text and 'computes' in text):
        scope['caller_note_required'] = True
    return scope


def private_issue_root(ctx):
    deps = ctx.deps
    return (ctx.run_id == deps.root_run_id and getattr(deps, 'visibility', None) == 'private'
            and getattr(deps, 'surface', None) == 'web' and not getattr(deps, 'swarm_id', None)
            and not getattr(deps, 'delegation_depth', 0) and not getattr(deps, 'delegation_generated', False))


def user_issue(ctx):
    if private_issue_root(ctx):
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


async def mailbox_request(request, directory):
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
    for _ in range(360):
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


async def start_relay(address, directory):
    async def handle(reader, writer):
        try:
            try:
                line = await asyncio.wait_for(reader.readline(), 5)
                if not line:  # Health probe or disconnected controller.
                    return
                request = json.loads(line)
                if (not isinstance(request, dict) or set(request) != {'action','number','session','turn','target'}
                        or not matches_target(request)
                        or request['action'] not in ('open','publish') or type(request['number']) is not int
                        or not 0 < request['number'] < 10**10
                        or not all(isinstance(request[k], str) and str(uuid.UUID(request[k])) == request[k]
                                   for k in ('session','turn'))):
                    raise ValueError('Invalid issue request.')
                response = {'result': await mailbox_request(request, directory)}
            except (OSError, ValueError) as error:
                response = {'error':str(error)}
            writer.write(json.dumps(response).encode() + b'\n')
            with contextlib.suppress(ConnectionError):
                await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    server = await asyncio.start_unix_server(handle, path=address, limit=1024)
    address.chmod(0o666)  # Only the controller shares the socket volume.
    return server


async def exchange(action, number, session, turn, *, address=SOCKET):
    target = selected_target()
    try:
        reader, writer = await asyncio.open_unix_connection(str(address), limit=256*1024)
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        raise ValueError('The local GitHub issue publisher is offline.') from exc
    try:
        writer.write(json.dumps(dict(action=action, number=number, session=session, turn=turn,
                                     target=target.identity)).encode() + b'\n')
        await writer.drain()
        response = json.loads(await asyncio.wait_for(reader.readline(), 95))
        if 'error' in response:
            raise ValueError(response['error'])
        return response['result']
    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    async with await start_relay(SOCKET, EXCHANGE) as server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main())
