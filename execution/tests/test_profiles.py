from pathlib import Path
import pytest
from workspace_runtime.profiles import profile_home, build_profile_config


def test_untrusted_profile_cannot_choose_home(tmp_path):
    for value in ('../review', '/tmp', '', 'other'):
        with pytest.raises(ValueError):
            profile_home(tmp_path, value)
    assert profile_home(tmp_path, 'review') == tmp_path / 'review'


def test_memory_endpoints_and_scopes_are_local_and_distinct():
    first = build_profile_config('development', {'gateway_key':'fixture', 'database_password':'fixture'})
    second = build_profile_config('review', {'gateway_key':'fixture', 'database_password':'fixture'})
    assert first['mem0']['mode'] == 'oss'
    assert first['mem0']['agent_id'] != second['mem0']['agent_id']
    for component in ('llm', 'embedder'):
        assert first['mem0']['oss'][component]['config']['openai_base_url'] == 'http://litellm:4000/v1'
    assert first['mem0']['oss']['vector_store']['config'] != second['mem0']['oss']['vector_store']['config']
