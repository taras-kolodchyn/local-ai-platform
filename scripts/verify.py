#!/usr/bin/env python3
"""Verify the supported lifecycle, always shut down, and prove owned resources stopped."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ('local-ai-platform', 'local-ai-moyai')
PHASES = (('lint', []), ('test', []), ('up', []), ('status', ['FORMAT=json']), ('smoke-test', []),
          ('moyai-smoke', ['RESTART=1']), ('repository-smoke', ['RESTART=1']))
HUB_PREFIXES = ('docker.io/', 'index.docker.io/', 'registry-1.docker.io/')


def announce(message: str) -> None:
    """A closed output consumer must not interrupt the shutdown guarantee."""
    try:
        print(message, flush=True)
    except BrokenPipeError:
        sys.stdout = open(os.devnull, 'w')


def normalize_reference(reference: str) -> str:
    """Match DMR's case-normalized tags without conflating repository namespaces."""
    reference = reference.strip().casefold()
    for prefix in HUB_PREFIXES:
        if reference.startswith(prefix):
            reference = reference[len(prefix):]
            break
    # DMR's default Docker Hub namespace is ai/. Its 1.2.9 ps table
    # abbreviates those references, while retaining other repository owners.
    if '/' not in reference:
        reference = 'ai/' + reference
    if '@' not in reference and ':' not in reference.rsplit('/', 1)[-1]:
        reference += ':latest'
    return reference


def resident_references(table: str) -> set[str]:
    """Read DMR 1.2.9's table-only ps output, including duplicate engine modes."""
    lines = [line.strip() for line in table.splitlines() if line.strip()]
    if not lines or not re.match(r'^MODEL\s+NAME\s+BACKEND\s+MODE\s+UNTIL\s*$', lines[0]):
        raise ValueError('Unrecognized Docker Model Runner resident inventory')
    references = set()
    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 4:
            raise ValueError('Incomplete Docker Model Runner resident inventory row')
        references.add(normalize_reference(fields[0]))
    return references


class Verification:
    def __init__(self) -> None:
        os.umask(0o077)
        directory = ROOT / '.local' / 'verification'
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory.chmod(0o700)
        prefix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
        self.directory = Path(tempfile.mkdtemp(prefix=prefix, dir=directory))
        self.signal: int | None = None
        self.phases: list[dict] = []
        self.failed_phase: str | None = None
        self.returncode = 0
        # Outer make flags must not turn required child checks into dry runs or
        # redirect them to another makefile. Every phase uses the real targets.
        self.environment = {key: value for key, value in os.environ.items()
                            if key not in ('MAKEFLAGS', 'MFLAGS', 'MAKEOVERRIDES')}

    def interrupted(self, signum: int, _frame: object) -> None:
        if self.signal is None:
            self.signal = signum

    def command(self, name: str, command: list[str], *, cancellable: bool = False,
                timeout: float | None = None) -> dict:
        log = self.directory / f'{name}.log'
        started = time.monotonic()
        terminated_at = None
        returncode = 1
        with log.open('w') as stream:
            try:
                process = subprocess.Popen(command, cwd=ROOT, env=self.environment,
                                           stdin=subprocess.DEVNULL, stdout=stream,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                while True:
                    elapsed = time.monotonic() - started
                    cancel = cancellable and self.signal is not None
                    expired = timeout is not None and elapsed >= timeout
                    if (cancel or expired) and terminated_at is None:
                        # Kill the phase's process group, including ongoing model
                        # clients/test operations, before supported cleanup starts.
                        self.stop_group(process, signal.SIGTERM)
                        terminated_at = time.monotonic()
                    elif terminated_at is not None and time.monotonic() - terminated_at >= 3:
                        self.stop_group(process, signal.SIGKILL)
                    try:
                        returncode = process.wait(timeout=0.1)
                        if terminated_at is not None or (cancellable and self.signal is not None):
                            # The make/shell parent can exit on TERM while one
                            # of its descendants ignores it. Reap that group too.
                            self.stop_group(process, signal.SIGKILL)
                        # A command may exit cleanly on TERM after it was timed out.
                        if expired:
                            returncode = 124
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except Exception:
                traceback.print_exc(file=stream)
        return {'name': name, 'returncode': returncode,
                'seconds': round(time.monotonic() - started, 3), 'log': str(log)}

    @staticmethod
    def stop_group(process: subprocess.Popen, signum: int) -> None:
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass

    def phase(self, name: str, arguments: list[str], *, cleanup: bool = False) -> dict:
        announce(f'verify: {name} (log: {self.directory / (name + ".log")})')
        result = self.command(name, ['make', '--no-print-directory', name, *arguments],
                              cancellable=not cleanup)
        self.phases.append(result)
        if result['returncode'] and self.failed_phase is None:
            self.failed_phase = name
            self.returncode = result['returncode'] if result['returncode'] > 0 else 1
        return result

    def configured_references(self) -> set[str]:
        resolver = ROOT / 'scripts' / 'model-config.py'
        if resolver.is_file():
            command = [sys.executable, str(resolver), 'resolve', '--format', 'json']
        else:
            # Keep the same precedence/scope as the supported shutdown, including
            # shutdown before first bootstrap. Never print or serialize secrets.
            command = ['bash', '-c', '''source scripts/lib.sh
set -a
source .env.example
set +a
if [[ -f .env ]]; then load_env; fi
python3 -c 'import json, os; print(json.dumps({"models": [{"reference": os.environ["QWEN_MODEL"]}, {"reference": os.environ["EMBEDDING_MODEL"]}]}))'
''']
        result = self.command('configured-models', command, timeout=30)
        if result['returncode']:
            raise ValueError('Could not resolve configured model references; see configured-models.log')
        configuration = json.loads(Path(result['log']).read_text())
        references = {normalize_reference(model['reference']) for model in configuration['models']}
        if not references:
            raise ValueError('Configured model references are missing')
        return references

    def stopped_inventory(self) -> dict:
        inventory: dict = {'passed': False, 'containers': {},
                           'resident_configured_models': [], 'errors': []}
        # Query project labels directly: Compose's active profiles or inherited
        # scope cannot hide an owned stopped container from this final check.
        for project in PROJECTS:
            result = self.command(f'containers-{project}', [
                'docker', 'ps', '-a', '--filter', f'label=com.docker.compose.project={project}',
                '--format', '{{.ID}}'], timeout=30)
            if result['returncode']:
                inventory['errors'].append(f'Could not inspect containers for {project}')
            else:
                inventory['containers'][project] = Path(result['log']).read_text().split()
        result = self.command('resident-models', ['docker', 'model', 'ps'], timeout=30)
        if result['returncode']:
            inventory['errors'].append('Could not inspect resident models')
        else:
            try:
                owned = self.configured_references()
                resident = resident_references(Path(result['log']).read_text())
                inventory['configured_models'] = sorted(owned)
                inventory['resident_configured_models'] = sorted(owned & resident)
            except (ValueError, KeyError, TypeError) as error:
                inventory['errors'].append(str(error))
        inventory['passed'] = not (inventory['errors'] or any(inventory['containers'].values())
                                   or inventory['resident_configured_models'])
        return inventory

    def run(self) -> int:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, self.interrupted)
        announce(f'verify: private logs: {self.directory}')
        inventory: dict = {'passed': False, 'errors': ['Final inventory did not complete']}
        try:
            for name, arguments in PHASES:
                if self.signal is not None:
                    break
                result = self.phase(name, arguments)
                if self.signal is not None:
                    if self.failed_phase is None:
                        self.failed_phase = name
                    break
                if result['returncode']:
                    break
        except Exception:
            self.failed_phase = self.failed_phase or 'runner'
            self.returncode = self.returncode or 1
            with (self.directory / 'runner-error.log').open('w') as stream:
                traceback.print_exc(file=stream)
        finally:
            self.phase('down', [], cleanup=True)
            try:
                inventory = self.stopped_inventory()
            except Exception:
                with (self.directory / 'inventory-error.log').open('w') as stream:
                    traceback.print_exc(file=stream)
            if not inventory['passed'] and self.failed_phase is None:
                self.failed_phase = 'stopped-state'
                self.returncode = 1
        if self.signal is not None:
            self.failed_phase = self.failed_phase or 'interrupted'
            self.returncode = self.returncode or 128 + self.signal
        report = {'status': 'failed' if self.failed_phase else 'passed',
                  'failed_phase': self.failed_phase, 'signal': self.signal,
                  'phases': self.phases, 'inventory': inventory,
                  'returncode': self.returncode}
        path = self.directory / 'result.json'
        path.write_text(json.dumps(report, indent=2) + '\n')
        if self.failed_phase:
            announce(f'verify: FAILED at {self.failed_phase}; down exit={self.phases[-1]["returncode"]}; '
                     f'stopped inventory={"passed" if inventory["passed"] else "failed"}')
        else:
            announce('verify: PASSED; owned containers and configured model allocations are absent')
        announce(f'verify: result: {path}')
        return self.returncode


if __name__ == '__main__':
    sys.exit(Verification().run())
