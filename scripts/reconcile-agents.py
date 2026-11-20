#!/usr/bin/env python3
"""Retire platform-owned legacy A2A registrations without deleting profile data."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ('development', 'review', 'research')
TEAM = 'workspace-knowledge'


def registry_at(local):
    path = local / 'workspace-registry.json'
    return json.loads(path.read_text()) if path.exists() else {}


def inventory(request):
    records = request('GET', '/v1/agents')
    return records.get('agents', records.get('data', [])) if isinstance(records, dict) else records


def owned_agents(records, registry):
    owned = []
    for record in records:
        for profile in PROFILES:
            if record.get('agent_name') != 'workspace-' + profile:
                continue
            card = record.get('agent_card_params') or {}
            if isinstance(card, str):
                try:
                    card = json.loads(card)
                except ValueError:
                    card = {}
            matches_registry = record['agent_id'] == registry.get('routes', {}).get(profile)
            matches_backend = isinstance(card, dict) and str(card.get('url', '')).rstrip('/') == (
                f'http://workspace-{profile}:8100/profiles/{profile}')
            if matches_registry or matches_backend:
                owned.append(record['agent_id'])
    return owned


def without_agents(permission, identifiers):
    if not isinstance(permission, dict):
        return None
    # The relationship id is returned by the API but is not a permission grant.
    result = {key: value for key, value in permission.items() if key != 'object_permission_id'}
    if isinstance(result.get('agents'), list):
        result['agents'] = [value for value in result['agents'] if value not in identifiers]
    return result


def owned_keys(request, local, *, include_owner=False):
    paths = [(local / 'workspace-client-key', 'workspace-console')]
    paths += [(local / 'profiles' / profile / 'gateway.key', 'workspace-' + profile) for profile in PROFILES]
    if include_owner:
        paths.append((local / 'workspace-owner-key', 'workspace-store-provisioning'))
    records = {}
    for path, alias in paths:
        if not path.is_file():
            continue
        key = path.read_text().strip()
        try:
            info = request('GET', '/key/info?key=' + hashlib.sha256(key.encode()).hexdigest())['info']
        except RuntimeError as exc:
            if str(exc).endswith('returned 404'):
                continue
            raise
        if info.get('key_alias') != alias or info.get('team_id') != TEAM:
            raise RuntimeError('Legacy key file refers to an unexpected key owner')
        records[hashlib.sha256(key.encode()).hexdigest()] = (key, info)
    # Local files may have been lost while dedicated keys still exist. Exact
    # reserved aliases AND the owned team identify those keys in the gateway.
    for alias in dict.fromkeys(alias for _, alias in paths):
        page = 1
        while True:
            query = urlencode({'team_id': TEAM, 'key_alias': alias, 'return_full_object': 'true',
                               'size': 100, 'page': page})
            response = request('GET', '/key/list?' + query)
            keys = response['keys']
            for listed in keys:
                if not isinstance(listed, dict) or listed.get('team_id') != TEAM or listed.get('key_alias') != alias:
                    raise RuntimeError('Gateway key inventory did not honor ownership filters')
                token = listed['token']
                if token not in records:
                    info = request('GET', '/key/info?key=' + token)['info']
                    if info.get('team_id') != TEAM or info.get('key_alias') != alias:
                        raise RuntimeError('Gateway key ownership changed during reconciliation')
                    records[token] = (token, info)
            if not keys or page >= response.get('total_pages', page):
                break
            page += 1
    return list(records.values())


def reconcile(request, local):
    local = Path(local)
    registry = registry_at(local)
    records = inventory(request)
    owned = owned_agents(records, registry)
    present = {record['agent_id'] for record in records}
    stale = set(registry.get('routes', {}).values()) - present
    keys = owned_keys(request, local, include_owner=True)
    associated = {info['agent_id'] for _, info in keys
                  if info.get('agent_id') and info['agent_id'] not in present}
    retired = set(owned) | stale | associated
    if retired:
        teams = request('GET', '/team/list')
        if isinstance(teams, dict):
            teams = teams.get('teams', teams.get('data', []))
        for team in teams:
            if team.get('team_id') != TEAM:
                continue
            permission = without_agents(team.get('object_permission'), retired)
            if permission is not None:
                request('POST', '/team/update', {'team_id': TEAM, 'object_permission': permission})
        for key, info in keys:
            update = {'key': key}
            permission = without_agents(info.get('object_permission'), retired)
            if permission is not None:
                update['object_permission'] = permission
            if info.get('agent_id') in retired:
                update['agent_id'] = None
            if len(update) > 1:
                request('POST', '/key/update', update)
        for identifier in owned:
            request('DELETE', '/v1/agents/' + identifier)
    if registry.get('routes'):
        registry['routes'] = {}
        path = local / 'workspace-registry.json'
        path.write_text(json.dumps(registry, indent=2) + '\n')
        path.chmod(0o600)
    check(request, local)
    return owned


def check(request, local):
    if owned_agents(inventory(request), registry_at(Path(local))):
        raise RuntimeError('Legacy workspace agent registrations remain in LiteLLM')


def docker_command(*args):
    return subprocess.run(['docker', *args], check=True, capture_output=True,
                          text=True, timeout=60).stdout.strip()


def stop_runtimes(docker=docker_command):
    for profile in PROFILES:
        identifiers = docker('ps', '--all', '--quiet', '--filter',
            'label=com.docker.compose.project=local-ai-platform', '--filter',
            'label=com.docker.compose.service=workspace-' + profile).splitlines()
        if identifiers:
            docker('stop', '--time', '10', *identifiers)
            docker('rm', *identifiers)


def purge(request, local, *, docker=docker_command):
    """Explicit migration only: delete precisely the three retired profiles' data."""
    local = Path(local).resolve()
    targets = [local / 'profiles' / profile for profile in PROFILES]
    targets += [local / 'execution', local / 'memory-passwords.json',
                local / 'workspace-client-key', local / 'workspace-identity.env']
    if any(not path.parent.resolve().is_relative_to(local) for path in targets):
        raise RuntimeError('Legacy data path escapes the local platform directory')
    keys = owned_keys(request, local)
    reconcile(request, local)
    stop_runtimes(docker)
    # These fixed names belong solely to the retired Mem0 profiles. Main
    # knowledge, LiteLLM and Moyai databases are not part of this migration.
    docker('compose', '--project-name', 'local-ai-platform', '--file', str(ROOT / 'compose.yaml'),
           'exec', '-T', 'postgres', 'psql', '-U', 'localai', '-d', 'local_ai',
           '-v', 'ON_ERROR_STOP=1',
           '-c', 'DROP DATABASE IF EXISTS workspace_memory WITH (FORCE)',
           '-c', 'DROP ROLE IF EXISTS memory_development, memory_review, memory_research')
    volumes = set(docker('volume', 'ls', '--format', '{{.Name}}').splitlines())
    retired_volumes = ['workspace_' + profile + '_state' for profile in PROFILES]
    present = [name for name in retired_volumes if name in volumes]
    if present:
        docker('volume', 'rm', *present)
    if keys:
        request('POST', '/key/delete', {'keys': [key for key, _ in keys]})
    for path in targets:
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    return {'volumes_removed': present, 'keys_revoked': len(keys),
            'database_removed': 'workspace_memory', 'profiles': list(PROFILES)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--stop-runtimes', action='store_true')
    parser.add_argument('--purge-data', action='store_true', help='Explicitly delete retired profile memory, volumes and local files')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('provision_workspace', ROOT / 'scripts/provision-workspace.py')
    provision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provision)
    request = provision.gateway(provision.master_key())
    if args.purge_data:
        print(json.dumps(purge(request, provision.LOCAL)))
    elif args.check:
        check(request, provision.LOCAL)
        print('Legacy workspace agent registrations are absent; Moyai uses its local UI integration')
    else:
        if args.stop_runtimes:
            stop_runtimes()
        removed = reconcile(request, provision.LOCAL)
        print(f'Retired {len(removed)} legacy workspace agent registrations; profile data preserved')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit('Agent reconciliation failed: ' + type(exc).__name__) from None
