#!/usr/bin/env python3
"""Resolve public model settings and render LiteLLM YAML without dependencies."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MODEL_KEYS = {'QWEN_MODEL', 'EMBEDDING_MODEL', 'QWEN_CONTEXT_SIZE', 'EMBEDDING_DIMENSIONS'}
REFERENCE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/:@+-]{0,254}\Z')
CATALOG = {
    'ai/qwen3-coder:30b-a3b-ud-q4_k_xl': (
        'Qwen3-Coder-30B-A3B-Instruct - local coding model for programming, '
        'file editing and tool calls in local clients and Moyai. '
        'MoE 30B-A3B; GGUF weights with UD-Q4_K_XL quantization.'),
    'ai/qwen3-embedding:0.6b-f16': (
        'Qwen3-Embedding-0.6B - local embedding model for semantic code search, '
        'retrieval and agent memory. 0.6B parameters; F16 weights, '
        'embedding vectors with up to 1024 dimensions.'),
}


def selected_values(path):
    """Parse only model fields as literals; never execute or expand dotenv text."""
    values = {}
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            key, separator, raw = line.strip().removeprefix('export ').partition('=')
            key = key.strip()
            if not separator or key not in MODEL_KEYS:
                continue
            try:
                parts = shlex.split(raw, comments=False, posix=True)
                # A shell comment must start a separate word. In model:tag#x,
                # the hash is literal and must fail reference validation.
                if len(parts) > 1 and parts[1].startswith("#"):
                    parts = parts[:1]
            except ValueError:
                raise ValueError(f'{key} must be a quoted or unquoted literal') from None
            if len(parts) != 1:
                raise ValueError(f'{key} must contain one nonempty literal')
            values[key] = parts[0]
    return values


def positive_integer(values, key):
    value = values.get(key, '')
    if not re.fullmatch(r'[0-9]+', value) or not 1 <= int(value) <= 2147483647:
        raise ValueError(f'{key} must be a positive integer up to 2147483647')
    return int(value)


def normalized_reference(value):
    """Docker Hub aliases and tag case refer to the same DMR model."""
    value = value.casefold()
    for prefix in ('docker.io/', 'index.docker.io/', 'registry-1.docker.io/'):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    # Docker Hub official models allow omitting ai/; DMR's resident table
    # uses that short form. Other explicit namespaces remain distinct.
    if '/' not in value:
        value = 'ai/' + value
    if ':' not in value.rsplit('/', 1)[-1] and '@' not in value:
        value += ':latest'
    return value


def resolve(root=ROOT):
    values = selected_values(root / '.env.example')
    source = '.env.example'
    if (root / '.env').is_file():
        values.update(selected_values(root / '.env'))
        source = '.env'
    context = positive_integer(values, 'QWEN_CONTEXT_SIZE')
    dimensions = positive_integer(values, 'EMBEDDING_DIMENSIONS')
    models = []
    for key, alias, mode in [('QWEN_MODEL', 'local-qwen', 'chat'),
                             ('EMBEDDING_MODEL', 'local-embeddings', 'embedding')]:
        reference = values.get(key, '')
        if not REFERENCE.fullmatch(reference):
            raise ValueError(f'{key} must be a Docker model reference without whitespace or shell syntax')
        description = CATALOG.get(normalized_reference(reference))
        if description is None:
            purpose = ('programming, file editing and tool calls' if mode == 'chat'
                       else f'semantic code search, retrieval and agent memory; configured dimensions: {dimensions}')
            description = (f'Local {mode} model for {purpose}. '
                           'Parameter count and weight format have not been verified for this reference.')
        description += f' Docker Model Runner reference: {reference}. Served on the Mac through LiteLLM.'
        models.append({'alias': alias, 'reference': reference, 'description': description,
                       'mode': mode, 'desired_context_size': context if mode == 'chat' else None,
                       'embedding_dimensions': dimensions if mode == 'embedding' else None})
    return {'configuration_source': source, 'models': models}


def render(root=ROOT, output=None):
    configuration = resolve(root)
    chat, embedding = configuration['models']
    substitutions = {
        '__QWEN_MODEL__': json.dumps('openai/' + chat['reference']),
        '__EMBEDDING_MODEL__': json.dumps('openai/' + embedding['reference']),
        '__QWEN_DESCRIPTION__': json.dumps(chat['description']),
        '__EMBEDDING_DESCRIPTION__': json.dumps(embedding['description']),
        '__QWEN_CONTEXT_SIZE__': str(chat['desired_context_size']),
    }
    template = (root / 'litellm/config.yaml').read_text(encoding='utf-8')
    # JSON strings are valid YAML scalars. Replace entire scalar markers, never
    # arbitrary substrings inside YAML, so values cannot introduce YAML structure.
    for marker, value in substitutions.items():
        if template.count(marker) != 1:
            raise ValueError(f'LiteLLM template must contain exactly one {marker} marker')
        template = template.replace(marker, value)
    destination = output or root / '.local/litellm/config.yaml'
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and not destination.is_symlink() and destination.read_text(encoding='utf-8') == template:
        destination.chmod(0o600)
        return destination
    descriptor, temporary = tempfile.mkstemp(prefix='.config-', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(template)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    resolver = commands.add_parser('resolve', help='Print resolved public model metadata')
    resolver.add_argument('--format', choices=['json'], default='json')
    renderer = commands.add_parser('render', help='Generate the effective LiteLLM configuration')
    renderer.add_argument('--output', type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command == 'resolve':
            print(json.dumps(resolve(), indent=2))
        else:
            print(render(output=arguments.output))
    except (OSError, ValueError) as error:
        print(f'Model configuration: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
