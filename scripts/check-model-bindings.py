#!/usr/bin/env python3
"""Check public LiteLLM model bindings against resolved local configuration."""
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
CONFIG = runpy.run_path(str(ROOT / 'scripts/model-config.py'))
ALIASES = ('local-qwen', 'local-embeddings')


def runtime_reference(value):
    if not isinstance(value, str) or not value:
        return None
    if value.startswith('openai/'):
        value = value[len('openai/'):]
    return CONFIG['normalized_reference'](value)


def verify_bindings(resolved, observed):
    """Validate only owned aliases. Never include private deployment data in errors."""
    records = observed.get('data') if isinstance(observed, dict) else None
    if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
        raise ValueError('LiteLLM model info response has no valid deployment list')
    configured = resolved.get('models') if isinstance(resolved, dict) else None
    if not isinstance(configured, list):
        raise ValueError('Resolved model configuration has no valid model list')
    for alias in ALIASES:
        expected = [row for row in configured if isinstance(row, dict) and row.get('alias') == alias]
        if len(expected) != 1:
            raise ValueError(f'{alias}: resolved configuration must contain exactly one model')
        deployments = [row for row in records if row.get('model_name') == alias]
        if len(deployments) != 1:
            raise ValueError(f'{alias}: LiteLLM must publish exactly one deployment')
        wanted, actual = expected[0], deployments[0]
        parameters = actual.get('litellm_params')
        info = actual.get('model_info')
        if not isinstance(parameters, dict) or not isinstance(info, dict):
            raise ValueError(f'{alias}: LiteLLM deployment metadata is incomplete')
        desired_reference = runtime_reference(wanted.get('reference'))
        if desired_reference is None or runtime_reference(parameters.get('model')) != desired_reference:
            raise ValueError(f'{alias}: runtime reference differs from resolved configuration')
        if not wanted.get('description') or info.get('description') != wanted['description']:
            raise ValueError(f'{alias}: English description differs from resolved configuration')
        if alias == 'local-qwen' and info.get('max_input_tokens') != wanted.get('desired_context_size'):
            raise ValueError(f'{alias}: configured context differs from resolved configuration')


def main():
    try:
        provision = runpy.run_path(str(ROOT / 'scripts/provision-workspace.py'))
        master_key = provision['master_key']()
        request = provision['gateway'](master_key)
        verify_bindings(CONFIG['resolve'](), request('GET', '/model/info'))
        # The dashboard rereads the configuration file on this route.
        for alias in (*ALIASES, 'local-reranker'):
            page = request('GET', f'/v2/model/info?model={alias}')
            if not any(row.get('model_name') == alias for row in page['data']):
                raise ValueError(f'{alias}: missing from the LiteLLM dashboard model list')
    except (OSError, KeyError, RuntimeError, ValueError, TypeError) as error:
        # Verification errors deliberately carry aliases and field names only.
        # Transport/credential failures are represented by type, never payloads.
        if isinstance(error, ValueError) and str(error).startswith((*ALIASES, 'local-reranker:', 'LiteLLM model info', 'Resolved model configuration')):
            detail = str(error)
        else:
            detail = type(error).__name__
        print('Model binding check failed: ' + detail, file=sys.stderr)
        return 1
    print('Model bindings and dashboard model list verified')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
