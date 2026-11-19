import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import SecretStr
from inference import local_model
from repository_workflow import RepositoryWorkflow
from repository_ledger import ARTIFACT_PATHS


class LocalContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_compaction_uses_deployment_budget_for_local_alias(self):
        settings = SimpleNamespace(gateway_url='http://localhost/v1', gateway_key=SecretStr('local-test'),
                                   model_timeout_seconds=30)
        with patch.dict('os.environ', {'QWEN_CONTEXT_SIZE':'32768'}):
            model, client = local_model(settings)
        try:
            self.assertEqual(model.context_window, 8192)
        finally:
            await client.close()

    def test_final_message_cites_recorded_policy_and_source(self):
        task = dict(phase='completed', completion_turn='turn', verified_tree='tree',
                    receipts=[{'state':'passed','tree_hash':'tree'}],
                    artifacts={p:'hash' for p in ARTIFACT_PATHS},
                    files={p:{'id':'file','sha256':'hash'} for p in ARTIFACT_PATHS},
                    citations=[dict(path='docs/architecture-rules.md',start_line=4,end_line=9),
                               dict(path='src/lib.rs',start_line=30,end_line=50)])
        message = RepositoryWorkflow.files_message(task,'turn')
        self.assertIn('docs/architecture-rules.md:4–9',message)
        self.assertIn('src/lib.rs:30–50',message)
