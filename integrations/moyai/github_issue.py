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
import uuid

REPO = 'taras-kolodchyn/local-ai-platform'
ISSUES = f'https://github.com/{REPO}/issues/'
PULLS = f'https://github.com/{REPO}/pull/'
EXCHANGE = Path('/run/moyai-github-issues')
SOCKET = Path('/run/moyai-publisher/issue.sock')


def issue_number(prompt):
    match = re.fullmatch(re.escape(ISSUES) + r'([1-9][0-9]{0,9})/?', prompt.strip()) if isinstance(prompt, str) else None
    return int(match[1]) if match else None


def issue_scope(number, turn, issue, binding):
    scope = {'number':number, 'turn':turn}
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
                if (not isinstance(request, dict) or set(request) != {'action','number','session','turn'}
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
    try:
        reader, writer = await asyncio.open_unix_connection(str(address), limit=256*1024)
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        raise ValueError('The local GitHub issue publisher is offline.') from exc
    try:
        writer.write(json.dumps(dict(action=action, number=number, session=session, turn=turn)).encode() + b'\n')
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
