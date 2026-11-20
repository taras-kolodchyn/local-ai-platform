"""Index and check the two public platform documents used by live retrieval probes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
# Public make index uses a directory basename. This scope cannot name a user checkout.
REPOSITORY = 'platform-checks/retrieval'
BRANCH = 'detached'
DOCUMENT = 'docs/offline.md'
SOURCES = (DOCUMENT, 'SECURITY.md')
QUERY = 'offline retrieval services on host loopback'
EVIDENCE = 'publishes services only on host loopback'


def index_documents():
    with tempfile.TemporaryDirectory(prefix='platform-retrieval-') as temporary:
        source = Path(temporary) / 'source'
        (source / 'docs').mkdir(parents=True)
        # The container's unprivileged ingestion user reads only this mounted subtree.
        source.chmod(0o755)
        (source / 'docs').chmod(0o755)
        for name in SOURCES:
            dest = source / name
            shutil.copyfile(ROOT / name, dest)
            dest.chmod(0o644)
        outcome = subprocess.run(
            ['docker', 'compose', '--project-name', 'local-ai-platform', '--file', 'compose.yaml',
             '--profile', 'tools', 'run', '--rm', '--no-deps', '--pull', 'never', 'ingestion', 'index', '/workspace',
             '--repository', REPOSITORY],
            cwd=ROOT, env={**os.environ, 'INDEX_REPO_PATH': str(source)},
            check=True, stdout=subprocess.PIPE, text=True)
    counts = json.loads(outcome.stdout)
    if (counts['repository'] != REPOSITORY or counts['branch'] != BRANCH
            or counts['files_indexed'] != len(SOURCES)
            or counts['chunks_embedded'] + counts['chunks_reused'] < len(SOURCES)):
        raise ValueError('Platform retrieval probe documents were not fully indexed')
    return counts


def check_citations(rows):
    lines = (ROOT / DOCUMENT).read_text().splitlines()
    found = False
    for row in rows:
        if (row['repository'], row['branch']) != (REPOSITORY, BRANCH) or row['path'] not in SOURCES:
            raise ValueError('Retrieval returned evidence outside the platform probe scope')
        if row['path'] != DOCUMENT:
            continue
        start, end = row['start_line'], row['end_line']
        # A score above either channel's rank-one contribution requires both cosine and keyword hits.
        if (1 <= start <= end <= len(lines) and EVIDENCE in row['content']
                and '\n'.join(lines[start - 1:end]).strip().startswith(row['content'])
                and row['relevance'] > 1 / 61):
            found = True
    if not found:
        raise ValueError('Hybrid retrieval did not cite the documented loopback binding policy')
    return len(rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['index', 'query', 'check'])
    action = parser.parse_args().action
    if action == 'index':
        print(json.dumps(index_documents()))
    elif action == 'query':
        print(json.dumps({'query': QUERY, 'repository': REPOSITORY, 'branch': BRANCH, 'limit': 3}))
    else:
        print(check_citations(json.load(sys.stdin)))
