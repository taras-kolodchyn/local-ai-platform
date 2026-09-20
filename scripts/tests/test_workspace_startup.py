import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class StartupTests(unittest.TestCase):
    def test_failure_stops_dependent_provisioning(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d); capture=directory/'events'
            docker=directory/'docker'
            docker.write_text('#!/bin/sh\necho docker >> "$EVENTS"\nexit 7\n')
            docker.chmod(0o755)
            python=directory/'python3'
            python.write_text('#!/bin/sh\necho provisioning >> "$EVENTS"\n')
            python.chmod(0o755)
            env=os.environ | {'PATH':str(directory)+':'+os.environ['PATH'],'EVENTS':str(capture)}
            result=subprocess.run(['bash','scripts/workspace-up.sh'],cwd=root,env=env,capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(capture.read_text().splitlines(),['docker'])
