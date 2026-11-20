import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from scripts import retrieval_probe as probe


class RetrievalProbeTests(unittest.TestCase):
    def test_incremental_probe_indexes_only_public_documents_and_removes_staging(self):
        captured = []

        def compose(argv, *, env, **kwargs):
            source = Path(env['INDEX_REPO_PATH'])
            captured.append(source)
            self.assertEqual(argv[-4:], ['index', '/workspace', '--repository', probe.REPOSITORY])
            self.assertEqual({p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file()},
                             set(probe.SOURCES))
            for name in probe.SOURCES:
                self.assertEqual((source / name).read_bytes(), (probe.ROOT / name).read_bytes())
            counts = dict(repository=probe.REPOSITORY, branch=probe.BRANCH, files_indexed=2,
                          chunks_embedded=0, chunks_reused=2)
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(counts))

        with patch.object(probe.subprocess, 'run', side_effect=compose):
            self.assertEqual(probe.index_documents()['chunks_reused'], 2)
        self.assertFalse(captured[0].exists())

    def test_hybrid_scope_content_and_source_coordinates_are_required(self):
        lines = (probe.ROOT / probe.DOCUMENT).read_text().splitlines()
        row = dict(repository=probe.REPOSITORY, branch=probe.BRANCH, path=probe.DOCUMENT,
                   start_line=1, end_line=len(lines), content='\n'.join(lines), relevance=2/61)
        self.assertEqual(probe.check_citations([row]), 1)
        for changes in ({'repository': 'user-checkout'}, {'branch': 'main'}, {'start_line': 4},
                        {'content': row['content'] + 'unindexed sentence'}, {'relevance': 1/61}):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                probe.check_citations([row | changes])

    def test_vector_probe_uses_its_own_store_on_a_populated_install(self):
        spec = importlib.util.spec_from_file_location('vector_smoke', probe.ROOT / 'scripts/vector-smoke.py')
        vector = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, retrieval_probe=probe):
            spec.loader.exec_module(vector)
        route = '/v1/vector_stores/vs_platform/search'
        forbidden = RuntimeError('Gateway returned 403')
        admin = Mock(side_effect=[{'key': 'probe'}, {}])
        scoped = Mock(side_effect=[{'data': []}, forbidden])
        client = Mock(side_effect=forbidden)
        keys = {'admin': admin, 'probe': scoped, 'client': client}
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary)
            (local / 'litellm-api-key').write_text('client')
            (local / 'litellm-master-key').write_text('admin')
            stores = {'user-checkout:main': 'vs_other', f'{probe.REPOSITORY}:{probe.BRANCH}': 'vs_platform'}
            (local / 'workspace-registry.json').write_text(json.dumps({'stores': stores}))
            with (patch.object(vector.provision, 'LOCAL', local), patch.object(vector.provision, 'vectors'),
                  patch.object(vector.provision, 'gateway', keys.__getitem__), patch.object(vector, 'check_citations'),
                  patch.object(sys, 'argv', ['vector-smoke.py', '--indexed'])):
                vector.main()
        generated = admin.call_args_list[0].args[2]
        self.assertEqual(generated['allowed_routes'], [route])
        self.assertEqual(generated['metadata']['allowed_vector_store_indexes'][0]['index_name'], 'vs_platform')
        self.assertEqual([item.args[1] for item in scoped.call_args_list],
                         [route, '/v1/vector_stores/vs_unregistered_probe/search'])
        self.assertEqual(client.call_args.args[1], route)
        self.assertEqual(admin.call_args, call('POST', '/key/delete', {'keys': ['probe']}))
