"""Small policy boundary for the opt-in local Moyai profile; no runtime secrets."""
import hashlib
import json
import re


def workspace_directory(session_id, authority_key):
    if not session_id or not authority_key:
        raise ValueError('A session and authority scope are required')
    identity = json.dumps([session_id, authority_key], separators=(',', ':')).encode()
    return '/workspace/' + hashlib.sha256(identity).hexdigest()


def validate_secrets(values):
    if not isinstance(values.get('password'), str) or len(values['password']) < 12:
        raise ValueError('Moyai requires an admin password of at least 12 characters')
    if not isinstance(values.get('api_key'), str) or not values['api_key']:
        raise ValueError('A scoped LiteLLM API key is required')


def valid_scoped_key(candidate, master, info):
    if not candidate or candidate == master or not isinstance(info, dict):
        return False
    if (not re.fullmatch(r'moyai-local-mvp(?:-[0-9a-f]{32})?', info.get('key_alias') or '')
            or info.get('models') != ['local-qwen']
            or set(info.get('allowed_routes') or []) != {'openai_routes', '/model/info'}
            or info.get('status') != 'active' or info.get('blocked')
            or info.get('team_id') or info.get('organization_id') or info.get('access_group_ids')):
        return False
    grants = info.get('object_permission')
    return isinstance(grants, dict) and all(
        not value for key, value in grants.items() if key != 'object_permission_id')
