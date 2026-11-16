#!/usr/bin/env python3
"""Verify the installed provider is actually activated inside every AIAgent."""
import json
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {'mem0_add', 'mem0_search', 'mem0_update', 'mem0_delete'}


def main():
    for profile in ('development', 'review', 'research'):
        result = subprocess.run(
            ['docker', 'compose', 'exec', '-T', 'workspace-' + profile,
             'python', '-m', 'workspace_runtime.worker'],
            input=json.dumps({'inspect': True, 'task_id': uuid.uuid4().hex}),
            text=True, capture_output=True, cwd=ROOT, timeout=90,
        )
        if result.returncode:
            raise RuntimeError(profile + ': agent initialization failed')
        report = json.loads(result.stdout)
        assert REQUIRED <= set(report['tools']), profile + ': Mem0 tools missing'
        assert report.get('memory_status') == 'ready', profile + ': Mem0 provider not ready'
        print(profile, 'agent Mem0 provider and four tools ready', flush=True)


if __name__ == '__main__':
    main()
