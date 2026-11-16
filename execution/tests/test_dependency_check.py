"""Only Hermes' exact, declared Pillow security override may bypass uv conflicts."""
import importlib.util
from pathlib import Path
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'check-dependencies.py'


def checker():
    spec = importlib.util.spec_from_file_location('dependency_check', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_report


LOCK = '''[manifest]
overrides = [{name = "pillow", specifier = "==13.0.0"}]
[[package]]
name = "pillow"
version = "13.0.0"
'''
REPORT = '''Checked 170 packages in 25ms
Found 1 incompatibility
The package `browser-harness` requires `pillow==12.3.0`, but `13.0.0` is installed
'''


def test_declared_pillow_security_override_is_accepted():
    assert checker()(REPORT, 1, LOCK) == 'upstream-pillow-override'


@pytest.mark.parametrize('report,status,lock', [
    (REPORT.replace('browser-harness', 'other-package'), 1, LOCK),
    (REPORT.replace('13.0.0', '12.3.0'), 1, LOCK),
    (REPORT.replace('Found 1 incompatibility\n', ''), 1, LOCK),
    (REPORT + 'The package `other` requires `unsafe==1`, but `2` is installed\n', 1, LOCK),
    (REPORT + 'error: could not read a package\n', 1, LOCK),
    (REPORT, 2, LOCK),
    (REPORT, 1, LOCK.replace('==13.0.0', '==12.3.0')),
])
def test_unaccounted_or_incomplete_checks_fail(report, status, lock):
    with pytest.raises(RuntimeError):
        checker()(report, status, lock)


def test_clean_uv_check_is_accepted():
    assert checker()('Checked 170 packages in 25ms\n', 0, LOCK) == 'clean'
