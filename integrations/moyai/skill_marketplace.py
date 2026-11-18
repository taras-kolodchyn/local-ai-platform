"""Wire format for the platform's text-only LiteLLM marketplace entries."""
import hashlib
import json
import re
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

TAG = 'moyai-text-v1'
MARKETPLACE = 'http://127.0.0.1:4000/claude-code/marketplace.json'
MAX_BYTES = 512 * 1024


def seed_hash(text):
    return 'moyai-seed-' + hashlib.sha256(text.encode()).hexdigest()


def read_marketplace(payload):
    skills = {}
    for entry in payload['plugins']:
        if TAG not in (entry.get('keywords') or []):
            continue
        name = entry['name']
        if name == 'addyosmani-test-driven-development':
            name = 'test-driven-development'
        summary, _, body = (entry.get('description') or '').replace('\r\n', '\n').partition('\n\n')
        if (not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name) or name in skills
                or not summary.strip() or '\n' in summary or not body.strip()):
            raise ValueError(f'Invalid text skill in the LiteLLM marketplace: {name}')
        skills[name] = {'description': summary.strip(), 'body': body.strip()}
    return skills


class LocalOnly(HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError('The local skill marketplace must not redirect')


def fetch_skills():
    with build_opener(ProxyHandler({}), LocalOnly()).open(MARKETPLACE, timeout=8) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('LiteLLM skill catalog exceeds the local snapshot size limit')
    return read_marketplace(json.loads(data))
