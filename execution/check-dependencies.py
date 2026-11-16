"""Check the runtime graph, accepting only Hermes' declared Pillow 13 override.

Hermes v0.23.1 intentionally overrides browser-harness' old Pillow pin in uv.lock.
Keep this exception narrower than upstream's general override checker.
"""
from pathlib import Path
import re
import subprocess
import tomllib

PILLOW_REPORT = 'The package `browser-harness` requires `pillow==12.3.0`, but `13.0.0` is installed'


def validate_report(report: str, status: int, lock_text: str) -> str:
    lines = [line.strip() for line in report.splitlines() if line.strip()]
    headers = [line for line in lines if re.fullmatch(r'Checked \d+ packages in .+', line)]
    rest = [line for line in lines if line not in headers]
    if len(headers) != 1:
        raise RuntimeError('Dependency check did not produce a complete package report')
    if status == 0 and not rest:
        return 'clean'
    lock = tomllib.loads(lock_text)
    declared = any(entry.get('name', '').lower() == 'pillow' and entry.get('specifier') == '==13.0.0'
                   for entry in lock.get('manifest', {}).get('overrides', []))
    locked = any(package.get('name') == 'pillow' and package.get('version') == '13.0.0'
                 for package in lock.get('package', []))
    if status == 1 and rest == ['Found 1 incompatibility', PILLOW_REPORT] and declared and locked:
        return 'upstream-pillow-override'
    raise RuntimeError('Unexpected dependency conflict or incomplete check')


def main():
    check = subprocess.run(['uv', 'pip', 'check'], capture_output=True, text=True)
    report = check.stdout + check.stderr
    print(report, end='')
    result = validate_report(report, check.returncode, Path('/opt/hermes/uv.lock').read_text())
    print('Runtime dependency validation:', result)


if __name__ == '__main__':
    main()
