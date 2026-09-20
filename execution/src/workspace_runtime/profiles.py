from pathlib import Path

PROFILES = ('development', 'review', 'research')


def profile_home(root: Path, profile: str) -> Path:
    if profile not in PROFILES:
        raise ValueError('Unknown profile')
    return root / profile


def build_profile_config(profile: str, secrets: dict[str, str]) -> dict:
    profile_home(Path('.'), profile)
    key = secrets['gateway_key']
    base = 'http://litellm:4000/v1'
    mem0 = {'mode': 'oss', 'user_id': 'workspace-user', 'agent_id': profile,
        'sync_max_chars': 3000, 'oss': {
        'llm': {'provider': 'openai', 'config': {'model': 'local-qwen', 'api_key': key,
            'openai_base_url': base, 'max_tokens': 1024}},
        'embedder': {'provider': 'openai', 'config': {'model': 'local-embeddings',
            'api_key': key, 'openai_base_url': base, 'embedding_dims': 1024}},
        'vector_store': {'provider': 'pgvector', 'config': {
            'dbname': 'workspace_memory', 'collection_name': 'memories',
            'embedding_model_dims': 1024, 'user': 'memory_' + profile,
            'password': secrets['database_password'], 'host': 'postgres',
            'port': 5432, 'minconn': 1, 'maxconn': 2}}}}
    config = {'_config_version': 44,
        'model': {'default': 'local-qwen', 'provider': 'custom', 'base_url': base,
                  'api_key': key, 'context_length': 65536},
        'memory': {'provider': 'mem0', 'memory_enabled': False, 'user_profile_enabled': False},
        'security': {'allow_lazy_installs': False},
        'auxiliary': {'background_review': {'enabled': False}},
        'mcp_servers': {
            'local_retrieval': {'url': 'http://litellm:4000/local_retrieval/mcp',
                'headers': {'Authorization': 'Bearer ' + key},
                'tools': {'include': ['local_retrieval-search_code', 'local_retrieval-get_chunk'],
                          'prompts': False, 'resources': False}},
            'local_tools': {'url': 'http://litellm:4000/local_tools/mcp',
                'headers': {'Authorization': 'Bearer ' + key},
                'tools': {'include': ['local_tools-list_files', 'local_tools-read_text_file',
                    'local_tools-git_status', 'local_tools-git_log', 'local_tools-postgres_select'],
                    'prompts': False, 'resources': False}}}}
    return {'config': config, 'mem0': mem0}
