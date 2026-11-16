"""The test launcher must reject unsupported Python before installing anything."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]


class TestPythonSelection(unittest.TestCase):
    def test_explicit_missing_interpreter_fails_before_pip(self):
        result = subprocess.run(
            ['bash', 'scripts/test-python.sh'], cwd=ROOT,
            env=os.environ | {'PYTHON': '/nonexistent/local-ai-python'},
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Python 3.11–3.14', result.stderr)

    def test_explicit_interpreter_is_validated(self):
        result = subprocess.run(
            ['bash', 'scripts/test-python.sh'], cwd=ROOT,
            env=os.environ | {'PYTHON': sys.executable},
            capture_output=True, text=True,
        )
        if (3, 11) <= sys.version_info[:2] < (3, 15):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), sys.executable)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Python 3.11–3.14', result.stderr)

    def test_automatic_selection_returns_a_supported_interpreter(self):
        env = os.environ.copy()
        env.pop('PYTHON', None)
        result = subprocess.run(
            ['bash', 'scripts/test-python.sh'], cwd=ROOT, env=env,
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        selected = subprocess.run(
            [result.stdout.strip(), '-c',
             'import sys; sys.exit(not ((3, 11) <= sys.version_info[:2] < (3, 15)))'],
            capture_output=True, text=True,
        )
        self.assertEqual(selected.returncode, 0, selected.stderr)
