#!/usr/bin/env python3
"""Read-only model and service observations for Docker Model Runner 1.2.9."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import platform
import re
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
# run_path avoids creating __pycache__: status must not create private state.
CONFIG = runpy.run_path(str(ROOT / 'scripts/model-config.py'))


def observe(arguments):
    try:
        result = subprocess.run(arguments, cwd=ROOT, text=True, capture_output=True, timeout=10)
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def resident_model_names(table):
    """DMR 1.2.9 supports table output only; never add format flags to ps."""
    lines = [line.strip() for line in table.splitlines() if line.strip()]
    if not lines or not re.match(r'^MODEL\s+NAME\s+', lines[0]):
        return None
    return {CONFIG['normalized_reference'](line.split()[0]) for line in lines[1:]}


def disk_size(value):
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value, False
    if not isinstance(value, str):
        return None, False
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*(B|KiB|MiB|GiB|TiB|KB|MB|GB|TB)', value)
    if not match:
        return None, False
    powers = {'B': 1, 'KiB': 1024, 'MiB': 1024**2, 'GiB': 1024**3, 'TiB': 1024**4,
              'KB': 1000, 'MB': 1000**2, 'GB': 1000**3, 'TB': 1000**4}
    try:
        return int(Decimal(match[1]) * powers[match[2]]), match[2] != 'B'
    except (ValueError, InvalidOperation):
        return None, False


def artifact(reference):
    result = observe(['docker', 'model', 'inspect', reference])
    try:
        payload = json.loads(result) if result else {}
        config = payload.get('config', {})
        if not isinstance(config, dict) or not payload.get('id'):
            raise ValueError('No artifact metadata')
        byte_count, estimated = disk_size(config.get('size'))
        # Only these public metadata fields may leave inspect. In particular,
        # GGUF tokenizer/chat templates and arbitrary environment data cannot.
        public = {'status': 'available', 'digest': payload.get('id'),
                  'format': config.get('format'), 'parameters': config.get('parameters'),
                  'quantization': config.get('quantization'),
                  'disk_size_display': config.get('size'), 'disk_size_bytes': byte_count,
                  'disk_size_bytes_estimated': estimated,
                  'size_source': 'docker model inspect config.size (artifact, not runtime memory)'}
        context = config.get('context_size')
        if not isinstance(context, int) or isinstance(context, bool) or context <= 0:
            context = None
        tags = payload.get('tags', [])
        names = [tag for tag in tags if isinstance(tag, str)] if isinstance(tags, list) else []
        return public, context, names
    except (ValueError, TypeError, AttributeError):
        return {'status': 'unavailable', 'note': 'Artifact is missing or Model Runner metadata is unavailable',
                'digest': None, 'format': None, 'parameters': None, 'quantization': None,
                'disk_size_display': None, 'disk_size_bytes': None,
                'disk_size_bytes_estimated': False}, None, []


def services(project):
    output = observe(['docker', 'ps', '--all', '--filter',
                      f'label=com.docker.compose.project={project}', '--format', '{{json .}}'])
    if output is None:
        return {'status': 'unavailable', 'project': project, 'containers': []}
    try:
        containers = []
        for line in output.splitlines():
            row = json.loads(line)
            containers.append({key.lower(): row.get(key) for key in ('Names', 'State', 'Status')})
        return {'status': 'available', 'project': project, 'containers': containers}
    except (ValueError, AttributeError):
        return {'status': 'unavailable', 'project': project, 'containers': []}


def linux_memory_total():
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemTotal:'):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def host_memory():
    if platform.system() == 'Darwin':
        output = observe(['sysctl', '-n', 'hw.memsize'])
        total = int(output.strip()) if output and output.strip().isdigit() else None
    else:
        total = linux_memory_total()
    output = observe(['ps', '-axo', 'pid=,rss=,comm='])
    processes = []
    if output:
        for line in output.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) != 3 or not all(part.isdigit() for part in parts[:2]):
                continue
            name = Path(parts[2]).name
            if name in {'llama-server', 'llama.cpp', 'docker-model-runner', 'model-runner',
                        'com.docker.llama-server'}:
                processes.append({'pid': int(parts[0]), 'name': name, 'rss_bytes': int(parts[1]) * 1024})
    return {'host_total_bytes': total, 'runner_process_rss': processes,
            'process_rss_status': 'measured' if processes else 'unavailable',
            'note': 'Host total is capacity. Process RSS is CPU/host resident memory, not a Metal allocation or per-model measurement.'}


def report():
    configuration = CONFIG['resolve']()
    table = observe(['docker', 'model', 'ps'])
    residents = resident_model_names(table) if table is not None else None
    models = []
    for model in configuration['models']:
        metadata, context, tags = artifact(model['reference'])
        candidates = [model['reference'], *tags]
        resident = (any(CONFIG['normalized_reference'](ref) in residents for ref in candidates)
                    if residents is not None else None)
        models.append(model | {'artifact': metadata, 'artifact_context_size': context,
                      'artifact_context_source': 'docker model inspect config.context_size (metadata, not effective runtime context)',
                      'runtime_context_size': None, 'runtime_context_status': 'unavailable',
                      'resident': resident,
                      'memory': {'metal_allocation_bytes': None, 'per_model_rss_bytes': None,
                                 'note': 'Metal allocation is not measured by this status command; host RSS and artifact size are different measurements.'}})
    return {'observed_at': datetime.now(timezone.utc).isoformat(),
            'configuration_source': configuration['configuration_source'],
            'runtime': {'status': 'available' if residents is not None else 'unavailable',
                        'resident_count': len(residents) if residents is not None else None,
                        'resident_source': 'docker model ps'},
            'services': [services('local-ai-platform'), services('local-ai-moyai')],
            'models': models, 'memory': host_memory(),
            'endpoints': {'LiteLLM': 'http://127.0.0.1:4000', 'Retrieval': 'http://127.0.0.1:8000',
                          'MCP gateway': 'http://127.0.0.1:4000/<server>/mcp',
                          'MCP debug': 'http://127.0.0.1:8001/mcp/', 'Grafana': 'http://127.0.0.1:3000',
                          'Prometheus': 'http://127.0.0.1:9090', 'Moyai': 'http://127.0.0.1:8787'}}


def display(report):
    print(f"Runtime: {report['runtime']['status']} (read-only observation)")
    print(f"Model configuration: {report['configuration_source']}")
    for project in report['services']:
        print(f"Services ({project['project']}): {project['status']}")
        for row in project['containers']:
            print(f"  {row['names']}: {row['state']} ({row['status']})")
        if project['status'] == 'available' and not project['containers']:
            print('  No owned containers')
    for model in report['models']:
        print(f"\n{model['alias']} -> {model['reference']}")
        print(f"  {model['description']}")
        residency = {True: 'yes', False: 'no', None: 'unavailable'}[model['resident']]
        print(f'  Resident: {residency}')
        desired = model['desired_context_size']
        metadata_context = model['artifact_context_size']
        print(f"  Desired runtime context: {desired if desired is not None else 'not configured'}; effective runtime context: unavailable")
        print(f"  Artifact context metadata: {metadata_context if metadata_context is not None else 'unavailable'} (not the effective runtime context)")
        artifact = model['artifact']
        print(f"  Artifact: {artifact['status']}; digest {artifact['digest'] or 'unavailable'}")
        print(f"  Weight metadata: {artifact['parameters'] or 'unavailable'}; {artifact['format'] or 'unavailable'}; {artifact['quantization'] or 'unavailable'}")
        print(f"  Artifact disk size (DMR): {artifact['disk_size_display'] or 'unavailable'}")
        print('  Metal allocation: unavailable (not measured; disk size and host RSS are not Metal usage)')
    memory = report['memory']
    print(f"\nHost total memory (capacity): {memory['host_total_bytes'] if memory['host_total_bytes'] is not None else 'unavailable'} bytes")
    print(f"Runner process RSS: {memory['process_rss_status']} (host memory, not per-model Metal allocation)")
    for process in memory['runner_process_rss']:
        print(f"  {process['name']} pid {process['pid']}: {process['rss_bytes']} bytes")
    print('\nEndpoints (configured addresses; availability is not probed):')
    for name, address in report['endpoints'].items():
        print(f'  {name}: {address}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--format', choices=['text', 'json'], default='text')
    arguments = parser.parse_args()
    try:
        result = report()
        if arguments.format == 'json':
            print(json.dumps(result, indent=2))
        else:
            display(result)
    except (OSError, ValueError) as error:
        print(f'Model status: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
