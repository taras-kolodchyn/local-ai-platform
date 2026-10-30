#!/usr/bin/env python3
"""Send one text task through the authenticated local gateway."""
import importlib.util
import json
from pathlib import Path
import sys
import uuid

spec = importlib.util.spec_from_file_location('provision', Path(__file__).with_name('provision-workspace.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ('development', 'review', 'research'):
        raise SystemExit('Usage: workspace.py development|review|research < task.txt')
    text = sys.stdin.read(16001)
    if not 1 <= len(text.strip()) <= 16000:
        raise SystemExit('Task must contain 1–16000 characters')
    registry = json.loads((p.LOCAL / 'workspace-registry.json').read_text())
    route = registry['routes'][sys.argv[1]]
    request = p.gateway((p.LOCAL / 'workspace-client-key').read_text().strip(), execution=True)
    result = request('POST', '/a2a/' + route, {'jsonrpc': '2.0', 'id': 1, 'method': 'message/send',
        'params': {'message': {'messageId': uuid.uuid4().hex, 'role': 'user',
            'parts': [{'kind': 'text', 'text': text}]}}})
    if 'error' in result:
        raise SystemExit('Execution request failed')
    task = result['result']
    for artifact in task.get('artifacts', []):
        for part in artifact.get('parts', []):
            if part.get('kind') == 'text':
                print(part['text'])
    if task.get('metadata', {}).get('memory_status') == 'degraded':
        print('Persistent memory unavailable for this task.', file=sys.stderr)
    if task['status']['state'] != 'completed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
