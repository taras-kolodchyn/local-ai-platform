"""Authenticated, bounded vector-store transport over existing knowledge."""
from __future__ import annotations

import hmac
import json
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route


def _filters(value, scope, depth=0):
    if value is None:
        return None
    if depth > 4 or not isinstance(value, dict):
        raise ValueError('Invalid filters')
    if value.get('type') == 'and' and set(value) == {'type', 'filters'}:
        children = value['filters']
        if not isinstance(children, list) or not 1 <= len(children) <= 10:
            raise ValueError('Invalid filters')
        paths = {p for child in children if (p := _filters(child, scope, depth + 1)) is not None}
        if len(paths) > 1:
            raise ValueError('Conflicting path filters')
        return next(iter(paths), None)
    if set(value) != {'type', 'key', 'value'} or value['type'] != 'eq':
        raise ValueError('Unsupported filter')
    key, expected = value['key'], value['value']
    if key not in ('repository', 'branch', 'path') or not isinstance(expected, str):
        raise ValueError('Unsupported filter')
    if not expected or len(expected) > 1000:
        raise ValueError('Invalid filter value')
    if key == 'path':
        return expected
    if expected != scope[key]:
        raise ValueError('Filter conflicts with store scope')
    return None


def _store(record):
    return {'id': record['id'], 'object': 'vector_store', 'name': record['repository'],
            'created_at': int(record.get('created_at', 0)), 'status': 'completed',
            'usage_bytes': 0, 'file_counts': {'in_progress': 0, 'completed': 0,
            'failed': 0, 'cancelled': 0, 'total': 0},
            'metadata': {'repository': record['repository'], 'branch': record['branch']}}


def vector_routes(key_file: Path, list_records, search_records):
    async def endpoint(request):
        try:
            key = key_file.read_text().strip()
        except OSError:
            key = ''
        supplied = request.headers.get('authorization', '')
        if not key or not hmac.compare_digest(supplied.encode(), ('Bearer ' + key).encode()):
            return JSONResponse({'error': 'Unauthorized'}, status_code=401)
        try:
            records = await run_in_threadpool(list_records)
            records = sorted(records, key=lambda r: r['id'])
            store_id = request.path_params.get('store_id')
            if store_id is None:
                if set(request.query_params) - {'after', 'limit', 'order'}:
                    raise ValueError('Unsupported pagination')
                limit = int(request.query_params.get('limit', '20'))
                if not 1 <= limit <= 100:
                    raise ValueError('Invalid limit')
                order = request.query_params.get('order', 'asc')
                if order not in ('asc', 'desc'):
                    raise ValueError('Invalid order')
                if order == 'desc':
                    records.reverse()
                after = request.query_params.get('after')
                if after:
                    ids = [r['id'] for r in records]
                    if after not in ids:
                        raise ValueError('Unknown cursor')
                    records = records[ids.index(after) + 1:]
                page = records[:limit]
                return JSONResponse({'object': 'list', 'data': [_store(r) for r in page],
                    'has_more': len(records) > limit,
                    'first_id': page[0]['id'] if page else None,
                    'last_id': page[-1]['id'] if page else None})
            scope = next((r for r in records if r['id'] == store_id), None)
            if scope is None:
                return JSONResponse({'error': 'Unknown store'}, status_code=404)
            if request.method == 'GET':
                return JSONResponse(_store(scope))
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 65536:
                    return JSONResponse({'error': 'Body too large'}, status_code=413)
            data = json.loads(body)
            if not isinstance(data, dict) or set(data) - {
                'query', 'filters', 'max_num_results', 'rewrite_query', 'ranking_options'}:
                raise ValueError('Unsupported search fields')
            query = data.get('query')
            if not isinstance(query, str) or not 2 <= len(query.strip()) <= 4000:
                raise ValueError('Query must be 2–4000 characters')
            limit = 6 if data.get('max_num_results') is None else data['max_num_results']
            if type(limit) is not int or not 1 <= limit <= 20:
                raise ValueError('Invalid result limit')
            if data.get('rewrite_query') is not None and data['rewrite_query'] is not False:
                raise ValueError('Query rewriting is unsupported')
            ranking = data.get('ranking_options') or {}
            if not isinstance(ranking, dict) or set(ranking) - {'ranker', 'score_threshold'}:
                raise ValueError('Unsupported ranking options')
            if ranking.get('ranker', 'none') != 'none':
                raise ValueError('Reranking is unsupported')
            threshold = ranking.get('score_threshold', 0)
            if type(threshold) not in (int, float) or not 0 <= threshold <= 1:
                raise ValueError('Invalid threshold')
            path = _filters(data.get('filters'), scope)
            rows = await run_in_threadpool(search_records, scope, query, path, limit)
            result, remaining = [], 24000
            for row in rows[:limit]:
                score = min(1.0, max(0.0, float(row['similarity'])))
                if score < threshold or remaining <= 0:
                    continue
                content = row['content'][:remaining]
                remaining -= len(content)
                result.append({'file_id': f"chunk_{row['id']}", 'filename': row['path'],
                    'score': score, 'content': [{'type': 'text', 'text': content}],
                    'attributes': {k: row[k] for k in ('repository', 'branch',
                        'commit_hash', 'start_line', 'end_line')} | {'chunk_id': row['id']}})
            return JSONResponse({'object': 'vector_store.search_results.page',
                'search_query': query, 'data': result, 'has_more': False, 'next_page': None})
        except (ValueError, TypeError, UnicodeError):
            return JSONResponse({'error': 'Invalid vector request'}, status_code=400)
        except TimeoutError:
            return JSONResponse({'error': 'Vector search timed out'}, status_code=504)
        except Exception:
            return JSONResponse({'error': 'Vector backend unavailable'}, status_code=503)

    return [Route('/v1/vector_stores', endpoint, methods=['GET']),
            Route('/v1/vector_stores/{store_id}', endpoint, methods=['GET']),
            Route('/v1/vector_stores/{store_id}/search', endpoint, methods=['POST'])]
