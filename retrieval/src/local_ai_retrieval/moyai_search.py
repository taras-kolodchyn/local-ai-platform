"""Candidate selection and source budgeting for Moyai repository lookups."""
from collections import Counter
from pathlib import PurePosixPath

import httpx

CANDIDATES = 48
CONTEXT_CHARS = 24_000
GAP = '\n…\n'
CODE_SUFFIXES = frozenset(
    '.py .sh .bash .rs .go .js .mjs .ts .tsx .jsx .c .h .cpp .hpp .java .kt .cs '
    '.rb .php .swift .sql .toml .yaml .yml .proto .css .html .json'.split()
)
NON_CODE_DIRS = {'doc', 'docs', 'test', 'tests', '__tests__', 'fixtures', 'testdata'}


def implementation(row):
    path = PurePosixPath(row['path'].lower())
    return (not NON_CODE_DIRS.intersection(path.parts[:-1])
            and not path.name.startswith(('test_', 'test-'))
            and not path.stem.endswith(('_test', '.test', '.spec'))
            and not path.name.endswith('.lock')
            and (path.suffix in CODE_SUFFIXES or path.name in {'makefile', 'dockerfile', 'containerfile'}))


def needs_reranking(code):
    best = {}
    for row in code:
        path = row['path']
        best[path] = max(best.get(path, -1.0), row['similarity'])
    if len(best) < 2:
        return False
    top = sorted(best, key=best.get, reverse=True)[:6]
    gap = best[top[0]] - best[top[1]]
    mean_gap = (best[top[0]] - best[top[-1]]) / (len(top) - 1)
    return top[0] != code[0]['path'] or gap <= mean_gap


def rank_windows(settings, query, code):
    documents, owners = [], []
    for row in code:
        # The index normally produces <=12k-character chunks. Bound scoring of
        # legacy oversized single lines independently of the returned excerpt.
        body = row['content'][:12000]
        for start in range(0, len(body), 1300):
            documents.append(row['path'] + '\n' + body[start:start + 1600])
            owners.append(row)
            if start + 1600 >= len(body):
                break
    if not documents:
        return code
    with httpx.Client(trust_env=False, timeout=25) as client:
        response = client.post(settings.litellm_base_url + '/rerank',
            headers={'Authorization': 'Bearer ' + settings.api_key()},
            json={'model': 'local-reranker', 'query': query, 'documents': documents,
                  'return_documents': False})
        response.raise_for_status()
        order = [result['index'] for result in response.json()['results']]
    if any(type(i) is not int for i in order) or sorted(order) != list(range(len(owners))):
        raise RuntimeError('Local reranker returned invalid window indices')
    # Windows are sorted by relevance, so the first occurrence of a chunk
    # represents its highest-scoring window.
    return [owners[i] for i in order]


def select_chunks(settings, query, rows, limit):
    code = [row for row in rows if implementation(row)]
    rerank = needs_reranking(code)
    ranked = rank_windows(settings, query, code) if rerank else code
    selected, seen, counts = [], set(), Counter()
    for candidates, cap in ((ranked, 2), (rows, 2), (rows, limit)):
        for row in candidates:
            path = row['path']
            if row['id'] in seen or counts[path] >= cap:
                continue
            selected.append(row)
            counts[path] += 1
            seen.add(row['id'])
            if len(selected) == limit:
                break
        if len(selected) == limit:
            break

    if code and not rerank:
        leader = code[0]['path']
        support = [i for i, row in enumerate(rows[:6]) if row['path'] == leader]
        if len(support) >= 3:
            third = rows[support[2]]
            if third['id'] not in seen:
                ahead = {row['id'] for row in rows[:support[2]]}
                slot = next((i for i, row in enumerate(selected) if row['id'] not in ahead), len(selected))
                selected.insert(slot, third)
    return selected[:limit]


def head_tail(text, size):
    if len(text) <= size:
        return text
    if size <= len(GAP):
        return text[:size]
    room = size - len(GAP)
    head = text[:(room + 1) // 2]
    if not head.endswith('\n'):
        head = head.rpartition('\n')[0]
    start = len(text) - room // 2
    if text[start - 1] != '\n':
        newline = text.find('\n', start)
        start = len(text) if newline == -1 else newline + 1
    tail = text[start:]
    return GAP.join(part for part in (head, tail) if part)


def pack_chunks(rows, budget):
    if not rows:
        return []
    lengths = [len(row['content']) for row in rows]
    sizes = [min(lengths[0], budget)] + [0] * (len(rows) - 1)
    remaining = budget - sizes[0]
    pending = sorted(range(1, len(rows)), key=lengths.__getitem__)
    for j, i in enumerate(pending):
        share, extra = divmod(remaining, len(pending) - j)
        if lengths[i] > share:
            for order, index in enumerate(sorted(pending[j:])):
                sizes[index] = share + int(order < extra)
            break
        sizes[i] = lengths[i]
        remaining -= sizes[i]
    packed = []
    for row, size in zip(rows, sizes):
        if size:
            text = head_tail(row['content'], size)
            if not text and not packed and row is rows[0]:
                text = row['content'][:size]  # An oversized first line still yields source.
            if not text:
                continue  # Neither end contains a complete source line within its quota.
            packed.append(dict(row, content=text, truncated=len(row['content']) > size))
    return packed
