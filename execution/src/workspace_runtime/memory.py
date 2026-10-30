"""Profile-local semantic memory operations through the pinned runtime plugin."""
import json
import logging
import os
from pathlib import Path


def validate_storage():
    import psycopg
    config = json.loads((Path(os.environ['HERMES_HOME']) / 'mem0.json').read_text())
    store = config['oss']['vector_store']['config']
    if config.get('mode') != 'oss' or store['embedding_model_dims'] != 1024:
        raise RuntimeError('Unsupported memory configuration')
    params = {k: store[k] for k in ('dbname', 'user', 'password', 'host', 'port')}
    with psycopg.connect(**params, connect_timeout=3) as connection:
        row = connection.execute("SELECT atttypmod FROM pg_attribute WHERE attrelid=to_regclass('memories') AND attname='vector'").fetchone()
        if row and row[0] != 1024:
            raise RuntimeError('Existing memory dimensions differ; migration required')


def provider():
    validate_storage()
    from plugins.memory.mem0 import Mem0MemoryProvider
    logging.getLogger('mem0').setLevel(logging.CRITICAL)
    instance = Mem0MemoryProvider()
    instance.initialize('workspace-memory')
    if instance._backend is None:
        raise RuntimeError('Memory backend unavailable')
    return instance


def call(instance, name, args):
    result = json.loads(instance.handle_tool_call(name, args))
    if 'error' in result:
        raise RuntimeError('Memory operation failed')
    return result


def operate(action, text='', fact_id=''):
    instance = provider()
    try:
        if action == 'add':
            call(instance, 'mem0_add', {'content': text})
            found = call(instance, 'mem0_search', {'query': text}).get('results', [])
            match = next((r for r in found if r['memory'] == text), None)
            if not match:
                raise RuntimeError('Saved fact was not retrievable')
            return {'id': match['id'], 'status': 'saved'}
        if action == 'search':
            return call(instance, 'mem0_search', {'query': text})
        if action == 'update':
            return call(instance, 'mem0_update', {'memory_id': fact_id, 'text': text})
        if action == 'delete':
            return call(instance, 'mem0_delete', {'memory_id': fact_id})
        raise ValueError('Unknown memory operation')
    finally:
        instance._shutdown_backend()


if __name__ == '__main__':
    import sys
    logging.disable(logging.CRITICAL)
    data = json.load(sys.stdin)
    try:
        print(json.dumps(operate(**data)))
    except Exception:
        print(json.dumps({'error': 'Memory operation failed'}))
        raise SystemExit(1)
