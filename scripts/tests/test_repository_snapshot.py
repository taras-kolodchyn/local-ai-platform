import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


def load():
    spec = importlib.util.spec_from_file_location('repository_snapshot', Path(__file__).parents[1] / 'repository-snapshot.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


class RepositorySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        self.catalog = Path(self.temp.name) / 'catalog'
        git(self.root, 'init', '-q')
        git(self.root, 'config', 'user.name', 'Test')
        git(self.root, 'config', 'user.email', 'test@example.invalid')
        (self.root / 'Cargo.lock').write_bytes(b'version = 4\r\n')
        (self.root / 'src').mkdir()
        (self.root / 'src/lib.rs').write_text('pub fn old() {}\n')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-qm', 'base')

    def prepare(self, root=None):
        return load().prepare_snapshot(root or self.root, self.catalog, 'rust-retry')

    def test_smoke_pins_the_selected_clean_subtree_and_refuses_uncommitted_edits(self):
        spec = importlib.util.spec_from_file_location('prepare', Path(__file__).parents[1] / 'prepare-repository.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source = self.root / 'src'
        first = module.prepare_smoke_source(source, self.catalog)
        self.assertEqual(first, module.prepare_smoke_source(source, self.catalog))
        self.assertEqual(first['git']['head'], git(self.root, 'rev-parse', 'HEAD'))
        self.assertEqual(first['git']['subtree'], 'src')
        self.assertEqual([r['path'] for r in first['files']], ['lib.rs'])
        lib = source / 'lib.rs'
        lib.write_text('pub fn changed() {}\n')
        with self.assertRaisesRegex(ValueError, 'clean Git'):
            module.prepare_smoke_source(source, self.catalog)
        git(self.root, 'commit', '-qam', 'updated source')
        second = module.prepare_smoke_source(source, self.catalog)
        self.assertNotEqual(first['snapshot_id'], second['snapshot_id'])
        self.assertEqual(second['git']['head'], git(self.root, 'rev-parse', 'HEAD'))
        staged = self.catalog / 'snapshots' / first['snapshot_id'] / 'source/lib.rs'
        self.assertEqual(staged.read_text(), 'pub fn old() {}\n')

    def test_root_dirty_content_bytes_and_immutable_publication(self):
        (self.root / 'src/lib.rs').write_bytes(b'pub fn dirty() {}\r\n')
        first = self.prepare()
        self.assertTrue(first['git']['dirty'])
        self.assertEqual({f['path'] for f in first['files']}, {'Cargo.lock', 'src/lib.rs'})
        source = self.catalog / 'snapshots' / first['snapshot_id'] / 'source'
        self.assertEqual((source / 'src/lib.rs').read_bytes(), b'pub fn dirty() {}\r\n')
        self.assertEqual(self.prepare()['snapshot_id'], first['snapshot_id'])
        (self.root / 'src/lib.rs').write_text('changed')
        self.assertNotEqual(self.prepare()['snapshot_id'], first['snapshot_id'])
        self.assertEqual((source / 'src/lib.rs').read_bytes(), b'pub fn dirty() {}\r\n')
        self.assertNotIn(str(self.root), json.dumps(first))

    def test_subtree_detached_worktree_and_nested_ignore(self):
        (self.root / 'src/.gitignore').write_text('ignored.rs\n')
        (self.root / 'src/ignored.rs').write_text('ignored')
        manifest = self.prepare(self.root / 'src')
        self.assertEqual(manifest['git']['subtree'], 'src')
        self.assertEqual([f['path'] for f in manifest['files']], ['lib.rs'])
        linked = Path(self.temp.name) / 'linked'
        git(self.root, 'worktree', 'add', '--detach', str(linked), 'HEAD')
        detached = self.prepare(linked)
        self.assertEqual(detached['git']['branch'], 'detached')
        self.assertEqual(detached['repository_id'], self.prepare()['repository_id'])

    def test_guards_and_explicit_plain_mode(self):
        (self.root / 'linked.rs').symlink_to(self.root / 'src/lib.rs')
        (self.root / 'secret.py').write_text('private = True')
        (self.root / 'settings.py').write_text('api_key="' + 'sk-' + 'x' * 30 + '"')
        (self.root / '.env').write_text('secret')
        (self.root / '.local').mkdir()
        (self.root / '.local/key.json').write_text('private')
        original = self.prepare()
        self.assertEqual([f['path'] for f in original['files']], ['Cargo.lock', 'src/lib.rs'])
        (self.root / 'src/lib.rs').unlink()
        self.assertEqual([f['path'] for f in self.prepare()['files']], ['Cargo.lock'])
        self.assertTrue((self.catalog / 'snapshots' / original['snapshot_id'] / 'source/src/lib.rs').is_file())
        plain = Path(self.temp.name) / 'plain'; plain.mkdir()
        (plain / 'app.py').write_text('print(1)')
        with self.assertRaises(ValueError):
            self.prepare(plain)
        manifest = load().prepare_snapshot(plain, self.catalog, 'rust-retry', unversioned=True)
        self.assertEqual(manifest['git']['head'], 'unversioned')

    def test_identity_uses_staged_bytes(self):
        module = load()
        original = module.stage_sources
        def stage_then_change(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.root / 'src/lib.rs').write_text('mutated after copy')
            return result
        module.stage_sources = stage_then_change
        manifest = module.prepare_snapshot(self.root, self.catalog, 'rust-retry')
        import hashlib
        staged = self.catalog / 'snapshots' / manifest['snapshot_id'] / 'source'
        for row in manifest['files']:
            self.assertEqual(row['sha256'], hashlib.sha256((staged / row['path']).read_bytes()).hexdigest())

    def test_temporary_edit_or_omission_during_copy_is_dirty_even_after_restore(self):
        from unittest.mock import patch
        module = load()
        original_stage = module.stage_sources
        path = self.root / 'src/lib.rs'
        original = path.read_bytes()
        for contents in (b'pub fn edited() {}\n', None):
            with self.subTest(contents=contents):
                def stage(*args):
                    if contents is None:
                        path.unlink()
                    else:
                        path.write_bytes(contents)
                    try:
                        return original_stage(*args)
                    finally:
                        path.write_bytes(original)
                with patch.object(module, 'stage_sources', side_effect=stage):
                    manifest = module.prepare_snapshot(self.root, self.catalog, 'rust-retry')
                staged = self.catalog / 'snapshots' / manifest['snapshot_id'] / 'source/src/lib.rs'
                self.assertEqual(staged.read_bytes() if staged.exists() else None, contents)
                self.assertEqual(git(self.root, 'status', '--porcelain'), '')
                self.assertTrue(manifest['git']['dirty'])

    def test_provenance_uses_captured_commit_if_head_changes_before_index_inspection(self):
        from unittest.mock import patch
        module = load()
        pinned = git(self.root, 'rev-parse', 'HEAD')
        original_git = module.git
        def switch_after_pin(root, *args, **kwargs):
            result = original_git(root, *args, **kwargs)
            if args == ('rev-parse', 'HEAD'):
                (self.root / 'src/lib.rs').write_text('pub fn newer() {}\n')
                git(self.root, 'commit', '-qam', 'new base')
            return result
        with patch.object(module, 'git', side_effect=switch_after_pin):
            manifest = module.prepare_snapshot(self.root, self.catalog, 'rust-retry')
        self.assertEqual(manifest['git']['head'], pinned)
        self.assertTrue(manifest['git']['dirty'])

    def test_full_catalog_remains_readable_after_oversize_registration_is_refused(self):
        from unittest.mock import Mock
        module = load()
        manifest = self.prepare()
        indexed = {'repository_id': manifest['repository_id'], 'snapshot_id': manifest['snapshot_id'],
            'store_id': 'vs_old', 'generation_id': 'gen_old'}
        module.register_catalog(self.catalog, manifest, indexed)
        target = self.catalog / 'catalog.json'
        catalog = json.loads(target.read_bytes())
        catalog['padding'] = ''
        limit = 1 << 20  # ContextBridge accepts an inclusive 1 MiB wire limit.
        catalog['padding'] = 'x' * (limit - len(module.canonical(catalog)) - 1)
        full = module.canonical(catalog) + b'\n'
        self.assertEqual(len(full), limit)
        target.write_bytes(full)
        provision = Mock()
        module.register_catalog(self.catalog, manifest, indexed, before_publish=provision)
        provision.assert_called_once()
        provision.reset_mock()
        with self.assertRaisesRegex(ValueError, 'catalog'):
            module.register_catalog(self.catalog, manifest,
                indexed | {'store_id': 'vs_new', 'generation_id': 'gen_new'}, before_publish=provision)
        provision.assert_not_called()
        self.assertEqual(target.read_bytes(), full)

    def test_catalog_retains_old_pins_and_failed_registration_keeps_current(self):
        module = load()
        first = self.prepare()
        indexed = {'repository_id': first['repository_id'], 'snapshot_id': first['snapshot_id'],
                   'store_id': 'vs_first', 'generation_id': 'gen_first'}
        module.register_catalog(self.catalog, first, indexed)
        before = (self.catalog / 'catalog.json').read_bytes()
        with self.assertRaises(ValueError):
            module.register_catalog(self.catalog, first, indexed | {'snapshot_id': 'wrong'})
        self.assertEqual((self.catalog / 'catalog.json').read_bytes(), before)
        (self.root / 'src/lib.rs').write_text('new version')
        second = self.prepare()
        module.register_catalog(self.catalog, second, indexed | {'snapshot_id': second['snapshot_id'],
            'store_id': 'vs_second', 'generation_id': 'gen_second'})
        catalog = json.loads((self.catalog / 'catalog.json').read_text())
        self.assertEqual(catalog['repositories'][0]['generation_id'], 'gen_second')
        self.assertEqual([r['generation_id'] for r in catalog['indexed_generations']], ['gen_first', 'gen_second'])

    def test_operator_compose_argv_uses_only_staged_mount_and_canonical_config(self):
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('prepare', Path(__file__).parents[1] / 'prepare-repository.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        manifest = self.prepare()
        indexed = {'repository_id': manifest['repository_id'], 'snapshot_id': manifest['snapshot_id'],
                   'store_id': 'vs_first', 'generation_id': 'gen_first'}
        with patch.object(module, 'validate_snapshot_profile'), patch.object(module.subprocess, 'run') as run, patch.dict('os.environ', {'COMPOSE_FILE': 'evil.yaml'}):
            run.return_value.stdout = json.dumps(indexed)
            module.index_prepared(self.catalog, manifest, 'sha256:test')
        arguments, keywords = run.call_args_list[0]
        self.assertIn('--no-deps', arguments[0])
        self.assertIn('index-snapshot', arguments[0])
        self.assertNotIn('COMPOSE_FILE', keywords['env'])
        self.assertEqual(keywords['env']['INDEX_REPO_PATH'], str(self.catalog / 'snapshots' / manifest['snapshot_id'] / 'source'))

    def test_tampered_existing_snapshot_is_rejected(self):
        first = self.prepare()
        published = self.catalog / 'snapshots' / first['snapshot_id'] / 'source/src/lib.rs'
        published.write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'bytes changed'):
            self.prepare()

    def test_published_catalog_and_snapshot_are_readable_by_nonroot_consumers(self):
        module = load()
        manifest = self.prepare()
        module.register_catalog(self.catalog, manifest, {'repository_id': manifest['repository_id'],
            'snapshot_id': manifest['snapshot_id'], 'store_id': 'vs_one', 'generation_id': 'gen_one'})
        snapshot = self.catalog / 'snapshots' / manifest['snapshot_id']
        self.assertTrue(snapshot.stat().st_mode & 0o001)
        self.assertTrue((snapshot / 'manifest.json').stat().st_mode & 0o004)
        self.assertTrue((self.catalog / 'catalog.json').stat().st_mode & 0o004)

    def test_gateway_provision_failure_keeps_previous_catalog(self):
        module = load()
        first = self.prepare()
        indexed = {'repository_id': first['repository_id'], 'snapshot_id': first['snapshot_id'],
            'store_id': 'vs_old', 'generation_id': 'gen_old'}
        module.register_catalog(self.catalog, first, indexed)
        before = (self.catalog / 'catalog.json').read_bytes()
        (self.root / 'src/lib.rs').write_text('new version')
        second = self.prepare()
        def fail(row):
            raise RuntimeError('gateway unavailable')
        with self.assertRaises(RuntimeError):
            module.register_catalog(self.catalog, second, indexed | {'snapshot_id': second['snapshot_id'],
                'store_id': 'vs_new', 'generation_id': 'gen_new'}, before_publish=fail)
        self.assertEqual((self.catalog / 'catalog.json').read_bytes(), before)

    def test_singular_credential_names_are_excluded_without_token_patterns(self):
        for name in ('secret.json', 'credential.json', 'credentials.txt', 'secrets.yaml'):
            (self.root / name).write_text('{"password": "ordinary-private-value"}')
        self.assertEqual([r['path'] for r in self.prepare()['files']], ['Cargo.lock', 'src/lib.rs'])

    def test_git_external_filters_are_rejected_before_execution(self):
        marker = Path(self.temp.name) / 'filter-ran'
        (self.root / '.gitattributes').write_text('src/lib.rs filter=evil\n')
        git(self.root, 'config', 'filter.evil.clean', f'touch {marker}; cat')
        (self.root / 'src/lib.rs').write_text('modified')
        with self.assertRaisesRegex(ValueError, 'external Git filter'):
            self.prepare()
        self.assertFalse(marker.exists())

    def test_submodules_are_explicitly_rejected(self):
        git(self.root, 'update-index', '--add', '--cacheinfo', '160000,' + git(self.root, 'rev-parse', 'HEAD') + ',nested')
        with self.assertRaisesRegex(ValueError, 'submodule'):
            self.prepare()

    def test_unexpected_source_io_failure_aborts_publication(self):
        from unittest.mock import patch
        module = load()
        with patch.object(module, 'read_source', side_effect=PermissionError('unreadable source')):
            with self.assertRaises(PermissionError):
                module.prepare_snapshot(self.root, self.catalog, 'rust-retry')
        self.assertEqual(list((self.catalog / 'snapshots').glob('*/manifest.json')), [])

    def test_runtime_model_binding_drift_rejects_before_model_inspection(self):
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('prepare', Path(__file__).parents[1] / 'prepare-repository.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with patch.object(module, 'gateway_model_info', return_value={'data': []}), patch.object(module.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                module.embedding_identity()
            run.assert_not_called()

    def test_live_custom_catalog_rejected_before_indexing(self):
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('prepare', Path(__file__).parents[1] / 'prepare-repository.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with patch.object(module, 'index_prepared') as index, patch.object(module, 'embedding_identity', return_value='sha256:test') as identity:
            self.assertEqual(module.main(['--repo', str(self.root), '--catalog', str(self.catalog)]), 1)
            index.assert_not_called()
            identity.assert_not_called()

    def test_successful_registration_serializes_database_hook_and_catalog(self):
        import threading
        from types import SimpleNamespace
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('prepare', Path(__file__).parents[1] / 'prepare-repository.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        first = self.prepare()
        (self.root / 'src/lib.rs').write_text('second version')
        second = self.prepare()
        entered = threading.Event(); release = threading.Event(); second_entered = threading.Event()
        order = []; errors = []
        def backend(arguments, **kwargs):
            if arguments[0] == 'docker':
                is_first = first['snapshot_id'] in ' '.join(arguments)
                number = '1' if is_first else '2'
                order.append('index' + number)
                if is_first:
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError('test registration wait expired')
                else:
                    second_entered.set()
                selected = first if is_first else second
                return SimpleNamespace(stdout=json.dumps({'repository_id': selected['repository_id'],
                    'snapshot_id': selected['snapshot_id'], 'generation_id': 'gen_' + number, 'store_id': 'vs_' + number}))
            row = json.loads(Path(arguments[arguments.index('--registration') + 1]).read_text())
            order.append('hook' + row['generation_id'][-1])
            return SimpleNamespace(stdout='')
        def register(selected):
            try:
                module.index_prepared(self.catalog, selected, 'sha256:test')
            except Exception as exc:
                errors.append(exc)
        with patch.object(module, 'validate_snapshot_profile'), patch.object(module.subprocess, 'run', side_effect=backend):
            a = threading.Thread(target=register, args=(first,)); b = threading.Thread(target=register, args=(second,))
            a.start()
            self.assertTrue(entered.wait(3))
            b.start()
            try:
                self.assertFalse(second_entered.wait(0.1), 'second DB activation overtook first publication')
            finally:
                release.set(); a.join(5); b.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(order, ['index1', 'hook1', 'index2', 'hook2'])
        self.assertEqual(json.loads((self.catalog / 'catalog.json').read_text())['repositories'][0]['generation_id'], 'gen_2')

    def test_live_profile_is_validated_before_backend_indexing(self):
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('prepare', Path(__file__).parents[1] / 'prepare-repository.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        manifest = self.prepare()  # Generic bytes-only fixture lacks pinned package metadata.
        with patch.object(module.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                module.index_prepared(self.catalog, manifest, 'sha256:test')
            run.assert_not_called()
        self.assertFalse((self.catalog / 'catalog.json').exists())

    def test_mutated_git_configuration_and_attributes_cannot_execute_filter(self):
        module = load()
        marker = Path(self.temp.name) / 'mutated-filter-ran'
        original = module.reject_git_execution
        def mutate_after_inspection(root):
            original(root)
            git(root, 'config', 'filter.late.clean', f'touch {marker}; cat')
            (root / '.gitattributes').write_text('src/lib.rs filter=late\n')
            (root / 'src/lib.rs').write_text('pub fn new() {}\n')
        module.reject_git_execution = mutate_after_inspection
        manifest = module.prepare_snapshot(self.root, self.catalog, 'rust-retry')
        self.assertTrue(manifest['git']['dirty'])
        self.assertFalse(marker.exists(), 'dirty inspection executed the mutated Git filter')

    def test_unversioned_directory_enumeration_error_aborts_before_publication(self):
        from unittest.mock import patch
        module = load()
        (self.root / 'docs').mkdir()
        (self.root / 'docs/notes.md').write_text('required context')
        original = module.os.scandir
        def denied(path):
            if Path(path).name == 'docs':
                raise PermissionError('directory enumeration denied')
            return original(path)
        with patch.object(module.os, 'scandir', side_effect=denied):
            with self.assertRaises(PermissionError):
                module.prepare_snapshot(self.root, self.catalog, 'rust-retry', unversioned=True)
        self.assertFalse((self.catalog / 'snapshots').exists())

    def test_late_filter_configuration_on_tracked_attributes_is_never_executed(self):
        module = load()
        marker = Path(self.temp.name) / 'tracked-filter-ran'
        (self.root / '.gitattributes').write_text('src/lib.rs filter=late\n')
        git(self.root, 'add', '.gitattributes')
        git(self.root, 'commit', '-qm', 'tracked attributes without a driver')
        original = module.reject_git_execution
        def configure_after_inspection(root):
            original(root)
            git(root, 'config', 'filter.late.clean', f'touch {marker}; cat')
            (root / 'src/lib.rs').write_text('pub fn new() {}\n')
        module.reject_git_execution = configure_after_inspection
        manifest = module.prepare_snapshot(self.root, self.catalog, 'rust-retry')
        self.assertTrue(manifest['git']['dirty'])
        self.assertFalse(marker.exists())

    def test_executable_only_edit_respects_core_filemode(self):
        source = self.root / 'src/lib.rs'
        git(self.root, 'config', 'core.filemode', 'true')
        self.assertFalse(self.prepare()['git']['dirty'])
        source.chmod(0o755)
        self.assertTrue(self.prepare()['git']['dirty'])
        git(self.root, 'config', 'core.filemode', 'false')
        self.assertFalse(self.prepare()['git']['dirty'])

    def test_unchanged_large_tracked_file_is_clean_but_never_staged(self):
        source = self.root / 'large.txt'
        source.write_bytes(b'x' * 1_000_001)
        git(self.root, 'add', 'large.txt')
        git(self.root, 'commit', '-qm', 'large tracked source')
        manifest = self.prepare()
        self.assertFalse(manifest['git']['dirty'])
        self.assertNotIn('large.txt', {row['path'] for row in manifest['files']})
        with source.open('r+b') as stream:
            stream.seek(999_999); stream.write(b'changed')
        self.assertTrue(self.prepare()['git']['dirty'])

    def test_tracked_symlink_replaced_by_regular_file_is_dirty(self):
        source = self.root / 'link.py'
        source.symlink_to('src/lib.rs')
        git(self.root, 'add', 'link.py')
        git(self.root, 'commit', '-qm', 'tracked symbolic link')
        self.assertFalse(self.prepare()['git']['dirty'])
        source.unlink(); source.write_text('ordinary replacement')
        self.assertTrue(self.prepare()['git']['dirty'])

    def test_tracked_regular_file_replaced_by_symlink_is_dirty(self):
        source = self.root / 'src/lib.rs'
        source.unlink(); source.symlink_to('../Cargo.lock')
        self.assertTrue(self.prepare()['git']['dirty'])
