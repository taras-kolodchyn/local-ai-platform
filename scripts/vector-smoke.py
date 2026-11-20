#!/usr/bin/env python3
"""Exercise the real gateway vector route without printing source bodies."""
import argparse
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

from retrieval_probe import BRANCH, QUERY, REPOSITORY, check_citations, index_documents

spec = importlib.util.spec_from_file_location('provision', Path(__file__).with_name('provision-workspace.py'))
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


def require_forbidden(request, route, payload):
    try:
        request('POST', route, payload)
    except RuntimeError as exc:
        if not str(exc).endswith('returned 403'):
            raise
    else:
        raise AssertionError('An unauthorized vector search succeeded')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--indexed', action='store_true', help='The caller just indexed the platform probe documents')
    if not parser.parse_args().indexed:
        index_documents()
    provision.vectors()
    local = provision.LOCAL
    admin = provision.gateway((local / 'litellm-master-key').read_text().strip())
    stores = json.loads((local / 'workspace-registry.json').read_text())['stores']
    sid = stores[f'{REPOSITORY}:{BRANCH}']
    route = f'/v1/vector_stores/{sid}/search'
    key = admin('POST', '/key/generate', {'key_alias': 'workspace-vector-check-' + uuid4().hex,
        'team_id': provision.TEAM, 'models': ['local-embeddings'], 'allowed_routes': [route],
        'metadata': {'allowed_vector_store_indexes': [{'index_name': sid, 'index_permissions': ['read']}]}})['key']
    try:
        request = provision.gateway(key)
        payload = {'query': QUERY, 'max_num_results': 2}
        result = request('POST', route, payload)
        check_citations([{**item['attributes'], 'path': item['filename'],
                          'content': '\n'.join(block['text'] for block in item['content']),
                          'relevance': item['attributes']['rrf_relevance']}
                         for item in result['data']])
        denied = provision.gateway((local / 'litellm-api-key').read_text().strip())
        require_forbidden(denied, route, payload)
        require_forbidden(request, '/v1/vector_stores/vs_unregistered_probe/search', payload)
        print('Gateway vector search cited the platform policy and denied both out-of-scope searches')
    finally:
        admin('POST', '/key/delete', {'keys': [key]})


if __name__ == '__main__':
    main()
