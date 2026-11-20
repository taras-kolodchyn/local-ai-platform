#!/usr/bin/env python3
"""Exercise real Moyai agent tools, terminal tests and authenticated artifacts."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integrations/moyai'))
from client import MoyaiClient
from acceptance import valid_pytest_action, valid_workspace_states


def compose(*args):
    return subprocess.run(['docker', 'compose', '--project-name', 'local-ai-moyai',
                           '--file', 'compose.moyai.yaml', *args],
                          cwd=ROOT, capture_output=True, text=True, check=True).stdout


def shell_evidence(client, session, turn):
    progress = client.request(f'/api/sessions/{session}/progress/{turn}?include_activity=true')
    compacted = []
    for tool in progress.get('tools') or []:
        if tool['name'] != 'shell' or tool['status'] != 'completed':
            continue
        action = client.request(f'/api/sessions/{session}/progress/{turn}/actions/{tool["id"]}')
        if valid_pytest_action(action):
            return tool['id']
        if action.get('output') == '[tool result cleared]':
            compacted.append(tool['id'])
    if compacted:
        receipt = json.loads(compose('exec', '-T', '--user', '10001:10001', 'moyai', 'python',
                                     '/opt/moyai-local/tests/observe_pytest_receipt.py', turn, *compacted))
        if receipt in compacted:
            return receipt
    raise RuntimeError('No completed native pytest shell receipt with exit_code 0 was found')


def observe_workspace(session):
    return json.loads(compose('exec', '-T', '--user', '10001:10001', 'moyai', 'python',
                              '/opt/moyai-local/tests/observe_workspace.py', session))


def isolation_guards():
    for service in ('moyai', 'moyai-sandbox', 'moyai-temporal'):
        identity = compose('ps', '-q', service).strip()
        data = json.loads(subprocess.check_output(['docker', 'inspect', identity]))[0]
        for network in data['NetworkSettings']['Networks']:
            metadata = json.loads(subprocess.check_output(['docker', 'network', 'inspect', network]))[0]
            if not metadata['Internal']:
                raise RuntimeError(f'{service} has an external runtime network')
        if any(mount['Destination'] == '/var/run/docker.sock' for mount in data['Mounts']):
            raise RuntimeError('A Docker socket was mounted inside the local MVP')
    probe = '''import os, socket
assert not os.path.exists('/var/run/docker.sock')
assert not any(os.getenv(k) for k in ('MODAL_TOKEN_ID','MODAL_TOKEN_SECRET','AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN'))
try:
    socket.create_connection(('1.1.1.1',443),timeout=1).close()
except OSError:
    pass
else:
    raise SystemExit('Unexpected external runtime egress')
print('Runtime isolation passed')
'''
    for service in ('moyai', 'moyai-sandbox'):
        compose('exec', '-T', '--user', '10001:10001', service, 'python', '-c', probe)
    bridge = json.loads(subprocess.check_output(['docker', 'inspect', compose('ps', '-q', 'moyai-bridge').strip()]))[0]
    bindings = bridge['NetworkSettings']['Ports']['8787/tcp']
    if not bindings or any(binding['HostIp'] != '127.0.0.1' for binding in bindings):
        raise RuntimeError('Moyai is not published exclusively on IPv4 loopback')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--restart', action='store_true', help='Also restart the MVP to verify persisted state')
    args = parser.parse_args()
    os.chdir(ROOT)
    os.umask(0o077)
    subprocess.run([sys.executable, 'scripts/moyai-prepare.py', '--ui', 'check'], check=True)
    client = MoyaiClient()
    if b'fontshare.com' in client.request('/', raw=True):
        raise RuntimeError('The local UI still requests external fonts')
    client.login()
    session = client.session('Local MVP: files, terminal and pytest')
    turn, detail = client.turn(session, '''Work only in this conversation's local workspace.
Use the native file and shell tools. Do not browse, delegate or ask questions.
Create calc.py defining add(a, b) that returns a + b.
Create test_calc.py with exactly three pytest tests: add(2, 3) == 5,
add(-2, 2) == 0, and add(0, 0) == 0.
Execute exactly python -m pytest -q as a standalone foreground shell command,
without redirections or wrappers. The result includes an Output: path to its real
log. Afterwards use a separate shell call to cp that log to test-results.txt in
the current directory. Do not export the hidden log directly.
Check that the command succeeds. Call save_artifact separately for calc.py,
test_calc.py and test-results.txt so the user can download all three files.
Finish with a brief factual report.''')
    files = client.request(f'/api/sessions/{session}/files')
    artifacts = {item['name']: item for item in files}
    expected = ('calc.py', 'test_calc.py', 'test-results.txt')
    if not all(name in artifacts for name in expected):
        raise RuntimeError('Moyai did not export all required artifacts')
    bodies = {name: client.request('/api/files/' + artifacts[name]['id'], raw=True) for name in expected}
    for name in ('calc.py', 'test_calc.py'):
        ast.parse(bodies[name], filename=name)
    if '3 passed' not in bodies['test-results.txt'].decode():
        raise RuntimeError('The exported test report does not show three passing tests')
    first_shell = shell_evidence(client, session, turn)
    first_state = observe_workspace(session)
    expected_hashes = {name: hashlib.sha256(bodies[name]).hexdigest() for name in ('calc.py', 'test_calc.py')}
    if first_state['files'] != expected_hashes:
        raise RuntimeError('Trusted workspace files do not match the downloaded artifacts')
    print('Native files, pytest exit_code 0 and authenticated artifacts verified', flush=True)
    isolation_guards()
    print('Private runtime networks, blocked external egress and loopback publishing verified', flush=True)
    if args.restart:
        print('Restarting only the local Moyai MVP to verify persistence...', flush=True)
        compose('restart', 'moyai', 'moyai-sandbox', 'moyai-temporal', 'moyai-bridge')
        compose('up', '--detach', '--wait', '--wait-timeout', '180')
        client = MoyaiClient()
        client.login()
        for name in expected:
            if client.request('/api/files/' + artifacts[name]['id'], raw=True) != bodies[name]:
                raise RuntimeError('An encrypted artifact changed across the restart')
    continued_state = observe_workspace(session)
    if continued_state != first_state:
        raise RuntimeError('The actual workspace changed before any followup model turn')
    followup, _ = client.turn(session, '''Use the native shell to rerun exactly python -m pytest -q as a
standalone foreground command, without redirections or wrappers. This existing workspace
already has calc.py and test_calc.py: do not recreate or modify them. The result includes
an Output: path to its real log. Afterwards use a separate shell call to cp that log to
persistence-results.txt in the current directory, then save_artifact with path exactly
persistence-results.txt. Do not export the hidden log directly.
Do not browse or delegate.''')
    shell_evidence(client, session, followup)
    followup_files = client.request(f'/api/sessions/{session}/files')
    persisted = next((file for file in followup_files if file['name'] == 'persistence-results.txt'), None)
    if persisted is None:
        raise RuntimeError('The followup turn did not save the required native test artifact')
    if b'3 passed' not in client.request('/api/files/' + persisted['id'], raw=True):
        raise RuntimeError('The continued conversation did not rerun the existing tests successfully')
    other = client.session('Local MVP: separate conversation workspace')
    other_turn, _ = client.turn(other, '''Use the native shell tool to check that calc.py is absent from
your current working directory. If absent, use the native write_file tool to create isolation.txt
containing ABSENT and a newline, then call save_artifact for isolation.txt. Inspect the actual tool
results; do not print tool-call markup as text. Do not search other directories, browse or delegate.''')
    other_files = client.request(f'/api/sessions/{other}/files')
    isolated = next((file for file in other_files if file['name'] == 'isolation.txt'), None)
    if isolated is None:
        raise RuntimeError('The separate conversation did not save the required native isolation artifact')
    if client.request('/api/files/' + isolated['id'], raw=True).strip() != b'ABSENT':
        raise RuntimeError('A different conversation reused the first conversation workspace')
    other_state = observe_workspace(other)
    if not valid_workspace_states(first_state, observe_workspace(session), other_state, expected_hashes):
        raise RuntimeError('Trusted workspace persistence/session-separation observation failed')
    receipt = {
        'verified_at': datetime.now(timezone.utc).isoformat(),
        'moyai_commit': 'c375632aea66f6695b66158b5edf6ee4983dad72',
        'model_alias': 'local-qwen', 'session': session, 'turn': turn,
        'checks': {'native_agent_turn': 'passed', 'python_files': 'passed',
                   'litellm_ui_link': 'passed',
                   'pytest': '3 passed', 'authenticated_artifact_downloads': 'passed',
                   'native_shell_receipt': first_shell, 'runtime_isolation': 'passed',
                   'followup_tests': '3 passed', 'separate_conversation_directory': 'passed',
                   'restart_persistence': 'passed' if args.restart else 'not requested'},
        'trusted_workspace_observations': {'first': first_state, 'continued': continued_state, 'other': other_state},
        'followup_turn': followup, 'isolation_session': other, 'isolation_turn': other_turn,
        'artifacts': {name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()},
    }
    Path('.local/moyai/smoke-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
