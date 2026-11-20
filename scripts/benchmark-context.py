"""Run paired repository searches and streamed answers inside the ingestion service."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from statistics import median
import sys
import tempfile
from time import perf_counter
from uuid import uuid4

import httpx

from local_ai_retrieval.config import Settings
from local_ai_retrieval.db import delete_snapshot
from local_ai_retrieval.ingestion import index_repository
from local_ai_retrieval.moyai_search import GAP
from local_ai_retrieval.vector_registry import scoped_search

ROOT = Path(__file__).resolve().parents[1]
CORPUS = tuple('scripts/' + name + '.sh' for name in (
    'up', 'ensure-docker', 'compose', 'lib', 'bootstrap', 'doctor', 'status', 'down', 'pull-model'))
# Each answer must be supported by a cited source line actually shown to the model.
CASES = {
    'local-service': (
        'Which service does scripts/up.sh explicitly force-recreate? Return the service name.',
        ['litellm'], [('scripts/up.sh', '--force-recreate')]),
    'local-poll': (
        'In scripts/ensure-docker.sh, how many attempts are made after opening Docker Desktop, and how many '
        'seconds separate attempts? Return both integers in order and cite both settings.',
        ['90', '2'], [('scripts/ensure-docker.sh', 'seq 1 90'), ('scripts/ensure-docker.sh', 'sleep 2')]),
    'cross-named': (
        'Which script does scripts/up.sh invoke immediately after load_env, and how many times does '
        'scripts/ensure-docker.sh poll after opening Desktop? Return the relative path and the integer in order.',
        ['scripts/ensure-docker.sh', '90'], [('scripts/up.sh', 'bash scripts/ensure-docker.sh'),
                                           ('scripts/ensure-docker.sh', 'seq 1 90')]),
    'cross-implicit': (
        'Which function does scripts/compose.sh call inside its .env guard, and which inherited profiles '
        'variable does the sourced library unset? Return the function name and variable name in order.',
        ['load_env', 'COMPOSE_PROFILES'], [('scripts/compose.sh', '  load_env'), ('scripts/lib.sh', 'unset COMPOSE_PROFILES')]),
}


def render(rows, files):
    evidence, shown = [], {}
    for row in rows:
        path = row['path']
        start = row['start_line']
        original = '\n'.join(files[path][start - 1:row['end_line']])
        offset = 0
        evidence.append(path)
        for part in row['content'].split(GAP):
            index = original.index(part, offset)
            first = start + original.count('\n', 0, index)
            for line, fragment in enumerate(part.splitlines(), first):
                evidence.append(f'{line}: {fragment}')
                shown.setdefault(f'{path}:{line}', []).append(fragment)
            offset = index + len(part)
    return '\n'.join(evidence), shown


def grade(answer, shown, expected, anchors):
    try:
        value = json.loads(answer.strip().removeprefix('```json').removesuffix('```').strip())
        answers = value['answers']
        correct = isinstance(answers, list) and [str(item) for item in answers] == expected
        refs = value['citations']
        cited = isinstance(refs, list) and all(isinstance(ref, str) and ref in shown for ref in refs)
        cited = cited and all(any(ref.rpartition(':')[0] == path and
                             any(anchor in text for text in shown[ref]) for ref in refs) for path, anchor in anchors)
        return correct, cited
    except (ValueError, KeyError, TypeError):
        return False, False


def sample(client, settings, scope, files, case, focused, trial):
    question, expected, anchors = CASES[case]
    start = perf_counter()
    rows = scoped_search(settings, scope, question, None, 6, auto_rank=True, focus_mentions=focused)
    retrieved = perf_counter()
    evidence, shown = render(rows, files)
    # Balance the two prefix slots between arms and make each trial reproducible.
    prompt = (f'Trial {trial}. Use the numbered source lines. Output only JSON with '
              '"answers" (an ordered array of bare identifiers, paths or integers, with no extra text) and '
              '"citations" (an array of path:line strings). For each fact cite the exact line that implements it.\n'
              f'Question: {question}\n\n{evidence}')
    payload = {'model': 'local-qwen', 'temperature': 0, 'max_tokens': 150, 'stream': True,
               'stream_options': {'include_usage': True}, 'cache': {'no-cache': True, 'no-store': True},
               'messages': [{'role': 'user', 'content': prompt}]}
    chunks, usage, first = [], None, None
    with client.stream('POST', settings.litellm_base_url + '/chat/completions', json=payload) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith('data:') or line[5:].strip() == '[DONE]':
                continue
            event = json.loads(line[5:])
            for choice in event.get('choices', []):
                content = choice['delta'].get('content')
                if content:
                    if first is None:
                        first = perf_counter()
                    chunks.append(content)
            if event.get('usage'):
                usage = event['usage']
    end = perf_counter()
    if first is None or not usage:
        raise ValueError('Incomplete streaming response or missing token usage')
    correct, cited = grade(''.join(chunks), shown, expected, anchors)
    return {'retrieval_s': retrieved - start, 'first_token_s': first - start, 'total_s': end - start,
            'input_tokens': usage['prompt_tokens'], 'output_tokens': usage['completion_tokens'],
            'answer_ok': correct, 'citations_ok': cited}


def measure(commit):
    settings = Settings.from_env()
    repo = 'platform-checks/context-' + uuid4().hex
    scope = {'repository': repo, 'branch': 'detached'}
    files = {p: (ROOT / p).read_text().splitlines() for p in CORPUS}
    samples = []
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary)
        try:
            # Stage incrementally so setup only embeds one new file per batch.
            for path in files:
                (source / path).parent.mkdir(exist_ok=True)
                shutil.copyfile(ROOT / path, source / path)
                indexed = index_repository(source, settings, repository=repo)
            if indexed['branch'] != scope['branch'] or indexed['files_indexed'] != len(files):
                raise RuntimeError('Incomplete benchmark corpus')
            with httpx.Client(trust_env=False, timeout=180, headers={'Authorization': 'Bearer ' + settings.api_key()}) as client:
                for focused in (False, True):
                    sample(client, settings, scope, files, 'local-service', focused, f'warm-{int(focused)}')
                for iteration in range(4):
                    for index, case in enumerate(CASES):
                        order = (False, True) if (iteration + index) % 2 == 0 else (True, False)
                        for slot, focused in enumerate(order):
                            arm = 'focused' if focused else 'baseline'
                            result = sample(client, settings, scope, files, case, focused, f'{iteration}-{index}-{slot}')
                            samples.append(dict(case=case, pair=iteration + 1, arm=arm, **result))
                            print(f'{case} pair {iteration+1} {arm}: {result["total_s"]:.2f}s '
                                  f'answer={result["answer_ok"]} citations={result["citations_ok"]}', file=sys.stderr, flush=True)
        finally:
            delete_snapshot(settings, repo, scope['branch'])
    summary = {}
    for case in CASES:
        summary[case] = {}
        for arm in ('baseline', 'focused'):
            rows = [r for r in samples if r['case'] == case and r['arm'] == arm]
            summary[case][arm] = {k: median(r[k] for r in rows) for k in
                                  ('retrieval_s', 'first_token_s', 'total_s', 'input_tokens', 'output_tokens')}
            summary[case][arm].update(answers=sum(r['answer_ok'] for r in rows),
                                      citations=sum(r['citations_ok'] for r in rows),
                                      passed=sum(r['answer_ok'] and r['citations_ok'] for r in rows))
    return {'commit': commit, 'recorded_at': datetime.now(timezone.utc).isoformat(),
            'model': 'local-qwen', 'pairs_per_case': 4, 'summary': summary, 'samples': samples}


if __name__ == '__main__':
    print(json.dumps(measure(sys.argv[1]), indent=2))
