"""Bootstrap's temporary master credential must never become a client key."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ClientKeyTest(unittest.TestCase):
    def test_bootstrap_master_placeholder_is_replaced_by_scoped_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            for name in ('lib.sh', 'provision-client-key.sh'):
                shutil.copy(ROOT / 'scripts' / name, root / 'scripts' / name)
            (root / '.local').mkdir()
            (root / '.env').write_text('LITELLM_MASTER_KEY=fixture-master\n')
            (root / '.local/litellm-api-key').write_text('fixture-master')
            binary = root / 'bin'
            binary.mkdir()
            curl = binary / 'curl'
            curl.write_text('''#!/usr/bin/env python3
import json, sys
from pathlib import Path
if '--write-out' in sys.argv:
    print('200', end='')
else:
    Path('request.json').write_text(sys.argv[sys.argv.index('--data') + 1])
    print(json.dumps({'key': 'sk-fixture-scoped'}))
''')
            curl.chmod(0o755)
            result = subprocess.run(['bash', 'scripts/provision-client-key.sh'], cwd=root,
                env=os.environ | {'PATH': str(binary) + ':' + os.environ['PATH']},
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / '.local/litellm-api-key').read_text(), 'sk-fixture-scoped')
            payload = json.loads((root / 'request.json').read_text())
            self.assertEqual(payload['models'], ['local-qwen', 'local-embeddings', 'local-reranker'])
            self.assertEqual(payload['object_permission']['mcp_servers'], ['local_retrieval', 'local_tools'])
