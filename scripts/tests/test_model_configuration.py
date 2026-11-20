"""Exercise model rendering and read-only status against controlled Docker replies."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ModelConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'scripts').mkdir()
        (self.root / 'litellm').mkdir()
        for name in ('model-config.py', 'model-status.py', 'status.sh', 'lib.sh', 'bootstrap.sh'):
            source = ROOT / 'scripts' / name
            if source.exists():
                shutil.copy(source, self.root / 'scripts' / name)
        shutil.copy(ROOT / 'litellm/config.yaml', self.root / 'litellm/config.yaml')
        shutil.copy(ROOT / '.env.example', self.root / '.env.example')
        self.env = os.environ.copy()

    def run_config(self, *args, **environment):
        return subprocess.run([sys.executable, 'scripts/model-config.py', *args],
                              cwd=self.root, env=self.env | environment,
                              text=True, capture_output=True, timeout=10)

    def configure(self, extra=''):
        (self.root / '.env').write_text(
            'QWEN_MODEL=fixture/new-coder:Q8\nEMBEDDING_MODEL=fixture/new-embed:F16\n'
            'QWEN_CONTEXT_SIZE=32768\n' + extra)

    def test_env_models_override_shell_and_render_routing_context_and_fresh_metadata(self):
        self.configure('POSTGRES_PASSWORD="literal; $(touch should-not-exist)"\n')
        resolved = self.run_config('resolve', QWEN_MODEL='ambient/wrong:old')
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        models = json.loads(resolved.stdout)['models']
        self.assertEqual([m['reference'] for m in models],
                         ['fixture/new-coder:Q8', 'fixture/new-embed:F16'])
        self.assertEqual(models[0]['desired_context_size'], 32768)
        self.assertIn('fixture/new-coder:Q8', models[0]['description'])
        self.assertNotIn('Qwen3-Coder-30B', models[0]['description'])
        self.assertNotIn('literal', resolved.stdout)
        rendered = self.run_config('render')
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        text = (self.root / '.local/litellm/config.yaml').read_text()
        self.assertIn('model: "openai/fixture/new-coder:Q8"', text)
        self.assertIn('max_input_tokens: 32768', text)
        self.assertNotIn('Qwen3-Coder-30B', text)
        self.assertNotIn('literal', text)
        self.assertFalse((self.root / 'should-not-exist').exists())

    def test_missing_env_uses_repository_defaults_without_creating_env(self):
        result = self.run_config('resolve')
        self.assertEqual(result.returncode, 0, result.stderr)
        models = json.loads(result.stdout)['models']
        self.assertEqual(models[0]['reference'], 'ai/qwen3-coder:30B-A3B-UD-Q4_K_XL')
        self.assertIn('30B-A3B', models[0]['description'])
        self.assertIn('UD-Q4_K_XL', models[0]['description'])
        self.assertTrue(models[0]['description'].isascii())
        self.assertIn('1024', models[1]['description'])
        self.assertFalse((self.root / '.env').exists())
        self.assertFalse((self.root / '.local').exists())

    def test_partial_env_uses_the_same_defaults_for_resolver_and_lifecycle_shell(self):
        (self.root / '.env').write_text('EMBEDDING_MODEL=fixture/partial:F16\nQWEN_CONTEXT_SIZE=32768\n')
        result = subprocess.run(['bash', '-c',
            'source scripts/lib.sh; load_env; '
            'python3 -c \'import json,os; print(json.dumps({k:os.environ[k] for k in '
            '["QWEN_MODEL","EMBEDDING_MODEL","QWEN_CONTEXT_SIZE","COMPOSE_PROJECT_NAME"]}))\''],
            cwd=self.root, env=self.env | {'QWEN_MODEL': 'ambient/wrong:tag',
                'EMBEDDING_MODEL': 'ambient/also-wrong:tag', 'QWEN_CONTEXT_SIZE': '1',
                'COMPOSE_PROJECT_NAME': 'unrelated'}, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            'QWEN_MODEL': 'ai/qwen3-coder:30B-A3B-UD-Q4_K_XL',
            'EMBEDDING_MODEL': 'fixture/partial:F16', 'QWEN_CONTEXT_SIZE': '32768',
            'COMPOSE_PROJECT_NAME': 'local-ai-platform'})
        resolved = json.loads(self.run_config('resolve').stdout)
        self.assertEqual(resolved['models'][0]['reference'], 'ai/qwen3-coder:30B-A3B-UD-Q4_K_XL')
        self.assertEqual(resolved['models'][1]['reference'], 'fixture/partial:F16')

    def test_bootstrap_renders_the_same_active_refs_and_context(self):
        self.configure()
        result = subprocess.run(['bash', 'scripts/bootstrap.sh'], cwd=self.root,
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (self.root / '.local/litellm/config.yaml').read_text()
        self.assertIn('model: "openai/fixture/new-coder:Q8"', text)
        self.assertIn('model: "openai/fixture/new-embed:F16"', text)
        self.assertIn('max_input_tokens: 32768', text)

    def test_compose_inputs_change_only_for_the_affected_service(self):
        self.configure()
        shutil.copy(ROOT / 'compose.yaml', self.root / 'compose.yaml')
        shutil.copy(ROOT / 'scripts/compose.sh', self.root / 'scripts/compose.sh')
        def services():
            result = self.run_config('render')
            self.assertEqual(result.returncode, 0, result.stderr)
            generated = self.root / '.local'
            identity = (generated / 'litellm-config.env').read_text().strip()
            expected = hashlib.sha256((generated / 'litellm/config.yaml').read_bytes()).hexdigest()
            self.assertEqual(identity, f'LOCAL_AI_CONFIG_SHA256={expected}')
            output = subprocess.check_output(['bash', 'scripts/compose.sh', 'config', '--format', 'json'],
                                             cwd=self.root, text=True)
            effective = json.loads(output)['services']
            self.assertEqual(effective['litellm']['environment']['LOCAL_AI_CONFIG_SHA256'], expected)
            return effective

        initial = services()
        self.assertEqual(services(), initial)
        template = self.root / 'litellm/config.yaml'
        template.write_text(template.read_text().replace('Local CPU cross-encoder', 'Updated CPU cross-encoder'))
        edited = services()
        self.assertEqual({k for k in initial if initial[k] != edited[k]}, {'litellm'})
        self.configure('RETRIEVAL_MAX_LIMIT=8\n')
        env_changed = services()
        self.assertNotEqual(env_changed['retrieval'], edited['retrieval'])
        self.assertEqual(env_changed['litellm'], edited['litellm'])
        self.assertEqual(services(), env_changed)

    def test_unquoted_hash_is_not_silently_truncated_into_a_different_reference(self):
        self.configure('QWEN_MODEL=fixture/coder:tag#not-a-shell-comment\n')
        result = self.run_config('resolve')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('QWEN_MODEL', result.stderr)

    def test_invalid_refs_and_context_fail_without_writing_output_or_echoing_values(self):
        for field, value in [('QWEN_MODEL', "'bad:tag\\nmodel_list: []'"),
                             ('EMBEDDING_MODEL', "'bad model'"),
                             ('QWEN_CONTEXT_SIZE', '-1'), ('QWEN_CONTEXT_SIZE', 'nope')]:
            with self.subTest(field=field, value=value):
                self.configure(f'{field}={value}\n')
                result = self.run_config('render')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(field, result.stderr)
                self.assertNotIn(value, result.stderr)
                self.assertFalse((self.root / '.local/litellm/config.yaml').exists())

    def fake_docker(self, *, unavailable=False):
        binaries = self.root / 'bin'
        binaries.mkdir(exist_ok=True)
        executable = binaries / 'docker'
        executable.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path('calls.jsonl').open('a') as stream:
    stream.write(json.dumps(args) + '\\n')
if os.getenv('DOCKER_UNAVAILABLE'):
    print('sensitive daemon error must not leak', file=sys.stderr)
    raise SystemExit(1)
if args[:2] == ['model', 'ps']:
    assert len(args) == 2, 'DMR 1.2.9 ps has no format flags'
    print('MODEL NAME  BACKEND  MODE  UNTIL')
    print(os.getenv('RESIDENT_FIXTURE', 'docker.io/fixture/new-coder:q8  llama.cpp  completion  idle'))
elif args[:2] == ['model', 'inspect']:
    if args[2] == 'fixture/new-embed:F16':
        raise SystemExit(1)
    print(json.dumps({'id':'sha256:abc123','tags':['docker.io/fixture/new-coder:q8'],
          'config':{'format':'gguf','parameters':'7B','quantization':'Q8_0',
          'size':'7.5GiB','context_size':128000,
          'gguf':{'tokenizer.chat_template':'DO-NOT-EXPOSE-TEMPLATE'}}}))
elif args[:2] == ['ps', '--all']:
    assert 'label=com.docker.compose.project=local-ai-platform' in args or 'label=com.docker.compose.project=local-ai-moyai' in args
    print(json.dumps({'Names':'example','State':'exited','Status':'Exited (0)',
          'Labels':'DO-NOT-EXPOSE-LABELS','Command':'DO-NOT-EXPOSE-COMMAND'}))
else:
    raise SystemExit('Status attempted an unexpected operation: ' + repr(args))
''')
        executable.chmod(0o755)
        self.env['PATH'] = str(binaries) + os.pathsep + self.env['PATH']
        if unavailable:
            self.env['DOCKER_UNAVAILABLE'] = '1'

    def run_status(self, format='json'):
        return subprocess.run(['bash', 'scripts/status.sh'], cwd=self.root,
                              env=self.env | {'FORMAT': format, 'COMPOSE_PROJECT_NAME': 'unrelated',
                                              'COMPOSE_FILE': '/tmp/unrelated.yaml',
                                              'COMPOSE_PROFILES': 'connected'},
                              text=True, capture_output=True, timeout=15)

    def test_status_distinguishes_artifact_context_from_runtime_and_reports_residency(self):
        self.configure()
        self.fake_docker()
        before = {p.relative_to(self.root) for p in self.root.rglob('*')}
        result = self.run_status()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        coder, embedding = report['models']
        self.assertTrue(coder['resident'])
        self.assertIsNone(coder['runtime_context_size'])
        self.assertEqual(coder['runtime_context_status'], 'unavailable')
        self.assertEqual(coder['desired_context_size'], 32768)
        self.assertEqual(coder['artifact_context_size'], 128000)
        self.assertEqual(coder['artifact']['digest'], 'sha256:abc123')
        self.assertEqual(coder['artifact']['disk_size_bytes'], 8053063680)
        self.assertEqual(coder['artifact']['quantization'], 'Q8_0')
        self.assertEqual(embedding['artifact']['status'], 'unavailable')
        self.assertFalse(embedding['resident'])
        self.assertIsNone(coder['memory']['metal_allocation_bytes'])
        self.assertIn('not measured', coder['memory']['note'])
        self.assertNotIn('DO-NOT-EXPOSE', result.stdout)
        after = {p.relative_to(self.root) for p in self.root.rglob('*')}
        self.assertEqual(after - before, {Path('calls.jsonl')})
        self.assertFalse((self.root / '.local').exists())

    def test_status_matches_official_model_names_that_dmr_reports_without_ai_namespace(self):
        self.fake_docker()
        self.env['RESIDENT_FIXTURE'] = 'qwen3-coder:30b-a3b-ud-q4_k_xl  llama.cpp  completion  idle'
        result = self.run_status()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['models'][0]['resident'])
        self.assertFalse(report['models'][1]['resident'])

    def test_status_does_not_match_a_different_namespace_with_the_same_official_model_name(self):
        (self.root / '.env').write_text('QWEN_MODEL=unrelated/qwen3-coder:30B-A3B-UD-Q4_K_XL\n')
        self.fake_docker()
        self.env['RESIDENT_FIXTURE'] = 'qwen3-coder:30b-a3b-ud-q4_k_xl  llama.cpp  completion  idle'
        result = self.run_status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['models'][0]['resident'])

    def test_unavailable_runtime_is_explicit_and_status_does_not_create_env_or_private_files(self):
        self.fake_docker(unavailable=True)
        result = self.run_status()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['runtime']['status'], 'unavailable')
        self.assertIsNone(report['models'][0]['resident'])
        self.assertNotIn('sensitive daemon error', result.stdout + result.stderr)
        self.assertFalse((self.root / '.env').exists())
        self.assertFalse((self.root / '.local').exists())
        text = self.run_status('text')
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn('local-qwen', text.stdout)
        self.assertIn('Metal allocation: unavailable', text.stdout)
        self.assertIn('Runtime: unavailable', text.stdout)


if __name__ == '__main__':
    unittest.main()
