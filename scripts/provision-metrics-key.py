"""Register the existing Prometheus bearer with access only to the metrics route."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]


def provision(master, key):
    if not key.startswith('sk-metrics-') or key == master:
        raise ValueError('Invalid Prometheus key file')
    opener = build_opener(ProxyHandler({}))
    headers = {'Authorization': 'Bearer ' + master, 'Content-Type': 'application/json'}
    base = 'http://127.0.0.1:4000'
    routes = ['/metrics', '/metrics/']
    digest = hashlib.sha256(key.encode()).hexdigest()
    try:
        with opener.open(Request(base + '/key/info?key=' + digest, headers=headers), timeout=10) as res:
            info = json.load(res)['info']
        if set(info.get('allowed_routes') or []) != set(routes):
            raise ValueError('The existing Prometheus key has unexpected routes')
    except HTTPError as error:
        if error.code != 404:
            raise
        request = Request(base + '/key/generate', headers=headers, data=json.dumps({
            'key': key, 'allowed_routes': routes, 'models': ['no-inference'],
            'key_alias': 'local-prometheus-' + digest[:10],
        }).encode())
        with opener.open(request, timeout=10) as res:
            if json.load(res).get('key') != key:
                raise ValueError('LiteLLM did not register the expected metrics credential')
    with opener.open(Request(base + '/metrics/', headers={'Authorization': 'Bearer ' + key}), timeout=10) as res:
        if res.status != 200 or not res.read(1):
            raise ValueError('Prometheus credential failed its metrics check')
    print('Scoped Prometheus credential verified')


if __name__ == '__main__':
    try:
        master = subprocess.check_output(['bash', str(ROOT / 'scripts/read-admin-key.sh')], text=True).strip()
        provision(master, (ROOT / '.local/litellm-metrics-key').read_text().strip())
    except HTTPError as error:
        sys.exit(f'Prometheus provisioning returned HTTP {error.code}')
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit('Prometheus provisioning failed')
