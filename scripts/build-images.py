"""Build changed Compose images before the normal health-checked up."""
import fcntl
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from tempfile import NamedTemporaryFile


class Uncacheable(ValueError):
    """Let Compose handle inputs outside the supported local allowlist."""


def context_entries(context, rules):
    # The same default-deny allowlist controls both Docker's context and this
    # walk. Other valid Docker patterns fall back to an ordinary Compose build.
    lines = [s for line in rules.splitlines() if (s := line.strip()) and not s.startswith('#')]
    if lines[:1] != ['**']:
        raise Uncacheable()
    includes, excludes = [], []
    for rule in lines[1:]:
        if rule.startswith('!') and not excludes:
            pattern = rule[1:]
            if not re.fullmatch(r'(?:[\w.-]+/)*(?:[\w.*-]+|\*\*)', pattern):
                raise Uncacheable()
            if '**' in pattern and not pattern.endswith('/**'):
                raise Uncacheable()
            if any(part in ('.', '..') for part in pattern.split('/')):
                raise Uncacheable()
            includes.append(pattern)
        elif re.fullmatch(r'\*\*/[\w.*-]+', rule) and '**' not in rule[3:]:
            excludes.append(rule[3:])
        else:
            raise Uncacheable()

    entries = set()

    def visit(path, recursive):
        if path in entries or any(fnmatch.fnmatchcase(path.name, pat) for pat in excludes):
            return
        mode = path.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise Uncacheable()
        if stat.S_ISDIR(mode) and not recursive:
            raise Uncacheable()
        entries.add(path)
        if stat.S_ISDIR(mode):
            for child in path.iterdir():
                visit(child, True)

    parents = set()
    for pattern in includes:
        parent, _, leaf = pattern.rpartition('/')
        directory = context / parent
        for ancestor in (directory, *directory.parents):
            if ancestor == context:
                break
            if ancestor.is_symlink():
                raise Uncacheable()
            if ancestor.is_dir():
                parents.add(ancestor)
        if not directory.is_dir():
            continue
        if leaf == '**':
            if not parent:
                raise Uncacheable()
            visit(directory, True)
        else:
            for path in directory.glob(leaf):
                visit(path, False)
    return sorted(entries | parents)


def fingerprint(service):
    build = service['build']
    if set(build) - {'context', 'dockerfile'} or not service.get('image') or service.get('pull_policy') == 'build':
        return None
    context = Path(build['context'])
    if not context.is_absolute():
        return None
    dockerfile = context / build.get('dockerfile', 'Dockerfile')
    specific = dockerfile.with_name(dockerfile.name + '.dockerignore')
    ignore = specific if specific.is_file() else context / '.dockerignore'
    try:
        paths = context_entries(context, ignore.read_text())
        digest = hashlib.sha256()
        settings = [build, service['image'], service.get('platform'), os.getenv('DOCKER_DEFAULT_PLATFORM')]
        digest.update(json.dumps(settings, sort_keys=True).encode())
        for path in [ignore, dockerfile, *paths]:
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                content = ''
            elif stat.S_ISREG(mode):
                content = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                return None
            digest.update(json.dumps([str(path.relative_to(context)), mode, content]).encode())
        return digest.hexdigest()
    except (OSError, ValueError):
        return None


def image_ids(services):
    images = {}
    for name, service in services.items():
        if tag := service.get('image'):
            result = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag],
                                    capture_output=True, text=True)
            if result.returncode == 0:
                images[name] = result.stdout.strip()
    return images


def reconcile(compose, services, receipt):
    try:
        previous = json.loads(receipt.read_text())
    except (FileNotFoundError, ValueError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    inputs = {name: fingerprint(service) for name, service in services.items()}
    ids = image_ids(services)
    dirty = [name for name in services if not inputs[name] or not ids.get(name)
             or previous.get(name) != [inputs[name], ids[name]]]
    if not dirty:
        print('Compose images unchanged: ' + ', '.join(services))
        return
    print('Building Compose services: ' + ', '.join(dirty), flush=True)
    subprocess.run([*compose, 'build', *dirty], check=True)
    rebuilt = {name: services[name] for name in dirty}
    ids = {name: image for name, image in ids.items() if name not in rebuilt} | image_ids(rebuilt)
    for name in dirty:
        if inputs[name] and fingerprint(services[name]) != inputs[name]:
            raise SystemExit(f'Build inputs changed while building {name}. Run make up again.')
    state = {name: [inputs[name], ids[name]] for name in services if inputs[name] and ids.get(name)}
    with NamedTemporaryFile(mode='w', dir=receipt.parent, delete=False) as tmp:
        json.dump(state, tmp)
    os.replace(tmp.name, receipt)


def main():
    compose = ['docker', 'compose', *sys.argv[1:]]
    config = json.loads(subprocess.check_output([*compose, 'config', '--format', 'json']))
    services = {name: s for name, s in config['services'].items() if 'build' in s}
    if services:
        cache = Path('.local/compose-builds')
        cache.mkdir(parents=True, exist_ok=True)
        project = config['name']
        with (cache / (project + '.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            reconcile(compose, services, cache / (project + '.json'))


if __name__ == '__main__':
    main()
