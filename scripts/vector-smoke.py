#!/usr/bin/env python3
"""Exercise the real gateway vector route without printing source bodies."""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('provision', Path(__file__).with_name('provision-workspace.py'))
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)
local = provision.LOCAL
admin = provision.gateway((local / 'litellm-master-key').read_text().strip())
stores = json.loads((local / 'workspace-registry.json').read_text())['stores']
assert stores, 'No indexed stores'
sid = next(iter(stores.values()))
key = admin('POST', '/key/generate', {'key_alias': 'workspace-vector-check',
    'team_id': provision.TEAM, 'models': ['local-embeddings'],
    'metadata': {'allowed_vector_store_indexes': [{'index_name': sid, 'index_permissions': ['read']}]}})['key']
try:
    request = provision.gateway(key)
    result = request('POST', f'/v1/vector_stores/{sid}/search', {'query': 'repository architecture policy', 'max_num_results': 2})
    assert result['data'], 'Search returned no source evidence'
    assert result['data'][0]['attributes']['start_line'] > 0
    denied = provision.gateway((local / 'litellm-api-key').read_text().strip())
    try:
        denied('POST', f'/v1/vector_stores/{sid}/search', {'query': 'repository policy'})
    except RuntimeError as exc:
        assert '403' in str(exc), str(exc)
    else:
        raise AssertionError('Unscoped key accessed store')
    print('Gateway vector search, citations, and cross-scope denial passed')
finally:
    admin('POST', '/key/delete', {'keys': [key]})
