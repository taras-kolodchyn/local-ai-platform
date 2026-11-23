"""Enroll or refresh a read-only GitHub connection on the local controller."""
import argparse
import json
from pathlib import Path
import subprocess
from uuid import UUID

from github_app import Installation
from client import MoyaiClient
from github_reader import reference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--enroll', choices=('issue', 'review'))
    action.add_argument('--refresh', type=UUID, metavar='CONNECTION')
    parser.add_argument('--app-directory', type=Path)
    args = parser.parse_args()
    client = MoyaiClient()
    client.opener.addheaders = [('User-Agent', 'Repository Reader')]
    client.login()
    if args.refresh:
        connection = str(args.refresh)
        rows = client.request('/api/connections')
        if not any(r['id'] == connection and r['kind'] == 'github' and r['read_only']
                   and not r['shared'] and r['enabled'] for r in rows):
            parser.error('Choose an enabled private GitHub reader owned by this account.')
    app = Installation(args.app_directory)
    app.refresh(reader=True)
    if args.enroll:
        tool = 'issue_read' if args.enroll == 'issue' else 'pull_request_read'
        row = client.request('/api/connections', {
            'name': 'Repository ' + args.enroll + ' reader', 'kind': 'github',
            'read_only': True, 'shared': False, 'credential': reference(app.authority),
            'policy': {'allowed_tools': [tool], 'require_approval': False},
        })
        connection = row['id']
        print('Connection', connection, flush=True)
    grant = {'authority': app.authority, 'expires': app.expires, 'token': app.token}
    subprocess.run(['docker', 'compose', '--project-name', 'local-ai-moyai', '-f', 'compose.moyai.yaml',
                    'exec', '-T', '--user', 'workspace', 'moyai', 'python',
                    '/opt/moyai-local/github_reader.py', connection, 'enroll' if args.enroll else 'refresh'],
                   input=json.dumps(grant), text=True, check=True)
    print('Reader ready', connection)


if __name__ == '__main__':
    main()
