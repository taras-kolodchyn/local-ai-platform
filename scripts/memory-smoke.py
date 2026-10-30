#!/usr/bin/env python3
"""Test real plugin tools in separate processes and isolated profile containers."""
import json
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def memory(profile, action, **args):
    result = subprocess.run(['docker', 'run', '--rm', '-i', '--network', 'local-ai-platform_data',
        '--user', f'{os.getuid()}:{os.getgid()}', '-e', 'HERMES_HOME=/opt/data',
        '-e', 'HOME=/opt/data', '-e', 'MEM0_TELEMETRY=false',
        '-e', 'PYTHONPATH=/opt/workspace/src',
        '-v', f'workspace_{profile}_state:/opt/data',
        '-v', f'{ROOT}/.local/profiles/{profile}/mem0.json:/opt/data/mem0.json:ro',
        '-v', f'{ROOT}/.local/profiles/{profile}/config.yaml:/opt/data/config.yaml:ro',
        '-v', f'{ROOT}/execution/src:/opt/workspace/src:ro',
        '--entrypoint', 'python', 'workspace-runtime:0.1.0', '-m', 'workspace_runtime.memory'],
        input=json.dumps({'action': action, **args}), capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(f'Memory {action} failed for {profile}')
    return json.loads(result.stdout)


def main():
    marker = 'Fixture ' + uuid.uuid4().hex
    text = marker + ' release colour is amber.'
    fact = memory('development', 'add', text=text)['id']
    try:
        recalled = memory('development', 'search', text=marker).get('results', [])
        assert any(r['id'] == fact for r in recalled)
        other = memory('review', 'search', text=marker).get('results', [])
        assert not any(marker in r['memory'] for r in other)
        memory('development', 'update', fact_id=fact, text=marker + ' release colour is violet.')
        changed = memory('development', 'search', text=marker).get('results', [])
        assert any(r['id'] == fact and 'violet' in r['memory'] for r in changed)
    finally:
        memory('development', 'delete', fact_id=fact)
    recalled = memory('development', 'search', text=marker).get('results', [])
    assert not any(r['id'] == fact for r in recalled)
    print('Persistent recall, profile isolation, update, and deletion passed across fresh processes')


if __name__ == '__main__':
    main()
