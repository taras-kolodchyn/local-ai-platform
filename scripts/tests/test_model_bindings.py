"""Verify public LiteLLM model records without exposing private parameters."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ModelBindingTests(unittest.TestCase):
    def setUp(self):
        self.resolved = {'models': [
            {'alias': 'local-qwen', 'reference': 'ai/qwen3-coder:30B-A3B-UD-Q4_K_XL',
             'description': 'English coding model description.', 'desired_context_size': 65536},
            {'alias': 'local-embeddings', 'reference': 'ai/qwen3-embedding:0.6B-F16',
             'description': 'English embedding model description.', 'desired_context_size': None}]}
        self.observed = {'data': [
            {'model_name': 'local-qwen',
             'litellm_params': {'model': 'openai/ai/qwen3-coder:30B-A3B-UD-Q4_K_XL',
                                'api_key': 'SECRET-MUST-NOT-LEAK'},
             'model_info': {'description': 'English coding model description.',
                            'max_input_tokens': 65536}},
            {'model_name': 'local-embeddings',
             'litellm_params': {'model': 'openai/ai/qwen3-embedding:0.6B-F16'},
             'model_info': {'description': 'English embedding model description.'}},
            {'model_name': 'unrelated-alias', 'litellm_params': {'model': 'cloud/unrelated'}}]}

    def verifier(self):
        path = ROOT / 'scripts/check-model-bindings.py'
        self.assertTrue(path.is_file(), 'The model binding verifier has not been implemented')
        spec = importlib.util.spec_from_file_location('model_bindings', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.verify_bindings

    def test_current_deployments_and_unrelated_alias_are_accepted(self):
        verify = self.verifier()
        before = copy.deepcopy((self.resolved, self.observed))
        verify(self.resolved, self.observed)
        self.assertEqual((self.resolved, self.observed), before)
        self.observed['data'][0]['litellm_params']['model'] = 'openai/docker.io/qwen3-coder:30b-a3b-ud-q4_k_xl'
        verify(self.resolved, self.observed)

    def test_duplicate_alias_deployment_is_rejected_even_if_both_refs_match(self):
        self.observed['data'].append(copy.deepcopy(self.observed['data'][0]))
        with self.assertRaisesRegex(ValueError, 'local-qwen.*exactly one deployment'):
            self.verifier()(self.resolved, self.observed)

    def test_missing_alias_is_rejected(self):
        self.observed['data'].pop(1)
        with self.assertRaisesRegex(ValueError, 'local-embeddings.*exactly one deployment'):
            self.verifier()(self.resolved, self.observed)

    def test_stale_database_model_reference_is_rejected_without_private_parameters(self):
        self.observed['data'][0]['litellm_params']['model'] = 'openai/other-owner/stale:SECRET-MUST-NOT-LEAK'
        with self.assertRaisesRegex(ValueError, 'local-qwen.*runtime reference') as error:
            self.verifier()(self.resolved, self.observed)
        self.assertNotIn('SECRET-MUST-NOT-LEAK', str(error.exception))
        self.assertNotIn('other-owner', str(error.exception))

    def test_stale_context_is_rejected(self):
        self.observed['data'][0]['model_info']['max_input_tokens'] = 128000
        with self.assertRaisesRegex(ValueError, 'local-qwen.*configured context'):
            self.verifier()(self.resolved, self.observed)

    def test_stale_description_is_rejected(self):
        self.observed['data'][1]['model_info']['description'] = 'Description for a previous embedding model.'
        with self.assertRaisesRegex(ValueError, 'local-embeddings.*English description'):
            self.verifier()(self.resolved, self.observed)

    def test_malformed_response_fails_with_a_safe_error(self):
        for malformed in ({}, {'data': None}, {'data': ['unexpected-private-text']}, {'data': {}}):
            with self.subTest(malformed=malformed):
                with self.assertRaises(ValueError) as error:
                    self.verifier()(self.resolved, malformed)
                self.assertNotIn('unexpected-private-text', str(error.exception))


if __name__ == '__main__':
    unittest.main()
