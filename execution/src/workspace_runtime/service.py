"""Private A2A text transport for one isolated execution profile."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from .profiles import PROFILES
from .runner import Runner


from .protocol import card


def task_response(tid, result):
    return {'kind': 'task', 'id': tid, 'contextId': tid,
        'status': {'state': result['state']},
        'artifacts': [{'artifactId': tid + '-result',
            'parts': [{'kind': 'text', 'text': result['text']}]}],
        'metadata': {'memory_status': result['memory_status']}}


def create_app(profile, home, runner=None):
    if profile not in PROFILES:
        raise ValueError('Unknown profile')
    runner = runner or Runner(home, [sys.executable, '-m', 'workspace_runtime.worker'],
                            lock_dir=Path(os.getenv('WORKSPACE_LOCK_DIR', str(home))))

    async def health(request):
        return JSONResponse({'status': 'ok', 'profile': profile})

    async def endpoint(request):
        if request.path_params['profile'] != profile:
            return JSONResponse({'error': 'Unknown profile'}, status_code=404)
        try:
            key = (home / 'backend.key').read_text().strip()
        except OSError:
            key = ''
        if not key or not hmac.compare_digest(request.headers.get('authorization', '').encode(),
                                             ('Bearer ' + key).encode()):
            return JSONResponse({'error': 'Unauthorized'}, status_code=401)
        if request.method == 'GET':
            return JSONResponse(card(profile))
        try:
            caller = (home / 'caller.sha256').read_text().strip()
        except OSError:
            caller = ''
        if not caller or not hmac.compare_digest(hashlib.sha256(request.headers.get('x-workspace-key', '').encode()).hexdigest(), caller):
            return JSONResponse({'error': 'Execution access denied'}, status_code=403)
        request_id = None
        try:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 65536:
                    return JSONResponse({'error': 'Body too large'}, status_code=413)
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError()
            request_id = data.get('id')
            if data.get('jsonrpc') != '2.0' or type(request_id) not in (str, int):
                raise ValueError()
            method, params = data.get('method'), data.get('params', {})
            if not isinstance(params, dict):
                raise ValueError()
            if method == 'message/send':
                message = params['message']
                if not isinstance(message, dict) or message.get('role') != 'user':
                    raise ValueError()
                parts = message['parts']
                if not isinstance(parts, list) or not 1 <= len(parts) <= 20:
                    raise ValueError()
                if any(not isinstance(p, dict) or p.get('kind') != 'text'
                       or not isinstance(p.get('text'), str) for p in parts):
                    raise ValueError()
                text = '\n'.join(p['text'] for p in parts)
                if not 1 <= len(text.strip()) <= 16000:
                    raise ValueError()
                tid = message['messageId']
                if not isinstance(tid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', tid):
                    raise ValueError()
                result = await runner.run(tid, text)
            elif method in ('tasks/get', 'tasks/cancel'):
                tid = params['id']
                if not isinstance(tid, str):
                    raise ValueError()
                result = runner.get(tid)
                if result is None:
                    return JSONResponse({'jsonrpc': '2.0', 'id': request_id,
                        'error': {'code': -32001, 'message': 'Task not found'}})
                if method == 'tasks/cancel':
                    await runner.cancel(tid)
                    result = runner.get(tid)
            else:
                return JSONResponse({'jsonrpc': '2.0', 'id': request_id,
                    'error': {'code': -32601, 'message': 'Method not supported'}})
            return JSONResponse({'jsonrpc': '2.0', 'id': request_id, 'result': task_response(tid, result)})
        except (ValueError, TypeError, KeyError):
            return JSONResponse({'jsonrpc': '2.0', 'id': request_id,
                'error': {'code': -32602, 'message': 'Invalid text request'}})

    return Starlette(routes=[Route('/health', health),
        Route('/profiles/{profile}', endpoint, methods=['POST']),
        Route('/profiles/{profile}/.well-known/agent-card.json', endpoint, methods=['GET']),
        Route('/profiles/{profile}/.well-known/agent.json', endpoint, methods=['GET'])])


def app_factory():
    return create_app(os.environ['WORKSPACE_PROFILE'], Path(os.environ['HERMES_HOME']))
