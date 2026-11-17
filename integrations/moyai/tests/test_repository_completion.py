"""A verified Files delivery ends the native turn without another inference call."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from pydantic_ai.messages import ModelResponse, TextPart
from repository_ledger import ARTIFACT_PATHS
from repository_workflow import RepositoryWorkflow


class RepositoryCompletionTests(unittest.IsolatedAsyncioTestCase):
    def setup_capability(self, task):
        ledger=SimpleNamespace(status=lambda session,authority:task)
        cap=RepositoryWorkflow(SimpleNamespace(ledger=ledger))
        ctx=SimpleNamespace(deps=SimpleNamespace(session_id='s',workspace_key='a',root_run_id='root',turn_id='turn'),
                            run_id='root',root_capability=SimpleNamespace(before_model_request=AsyncMock()))
        return cap,ctx

    def completed(self):
        digests={p:str(i)*64 for i,p in enumerate(ARTIFACT_PATHS)}
        return {'phase':'completed','completion_turn':'turn','pending':None,'verified_tree':'a'*64,
                'receipts':[{'state':'passed','tree_hash':'a'*64}],
                'artifacts':digests,'files':{p:{'id':f'file-{i}','sha256':d}
                    for i,(p,d) in enumerate(digests.items())}}

    async def test_completed_delivery_returns_final_text_without_model_or_budget_call(self):
        task=self.completed();task.update(model_calls=60,deadline=0)
        cap,ctx=self.setup_capability(task)
        cap.model_budget=AsyncMock()
        async def native_before(run,request):
            return await cap.before_model_request(run,request)
        ctx.root_capability.before_model_request.side_effect=native_before
        handler=AsyncMock(return_value=ModelResponse(parts=[TextPart('unexpected model call')]))
        result=await cap.wrap_model_request(ctx,request_context=object(),handler=handler)
        handler.assert_not_awaited()
        ctx.root_capability.before_model_request.assert_awaited_once()
        cap.model_budget.assert_not_awaited()
        self.assertEqual(result.provider_name,'local-controller')
        self.assertTrue(all(isinstance(p,TextPart) for p in result.parts))
        self.assertIn('Trusted checks passed',result.parts[0].content)
        for i,path in enumerate(ARTIFACT_PATHS):
            self.assertIn(path.split('/')[-1],result.parts[0].content)
            self.assertIn('/api/files/file-'+str(i),result.parts[0].content)

    async def test_native_authority_denial_blocks_completion_response(self):
        cap,ctx=self.setup_capability(self.completed())
        ctx.root_capability.before_model_request.side_effect=PermissionError('Native authority revoked')
        handler=AsyncMock()
        with self.assertRaises(PermissionError):
            await cap.wrap_model_request(ctx,request_context=object(),handler=handler)
        handler.assert_not_awaited()

    async def test_unverified_or_incomplete_delivery_cannot_synthesize_success(self):
        for phase in (None,'analyzing','implementing','verified','delivering','failed','uncertain'):
            task=self.completed();task['phase']=phase
            cap,ctx=self.setup_capability(task)
            expected=ModelResponse(parts=[TextPart('normal')]);handler=AsyncMock(return_value=expected)
            self.assertIs(await cap.wrap_model_request(ctx,request_context=object(),handler=handler),expected)
            handler.assert_awaited_once()
        for field,value in [('pending','inflight'),('verified_tree',None),('files',{}),('receipts',[])]:
            task=self.completed();task[field]=value
            cap,ctx=self.setup_capability(task)
            expected=ModelResponse(parts=[TextPart('normal')]);handler=AsyncMock(return_value=expected)
            self.assertIs(await cap.wrap_model_request(ctx,request_context=object(),handler=handler),expected)

    async def test_foreign_run_or_mismatched_file_hash_cannot_claim_completion(self):
        task=self.completed();task['files'][ARTIFACT_PATHS[0]]['sha256']='f'*64
        cap,ctx=self.setup_capability(task)
        expected=ModelResponse(parts=[TextPart('normal')]);handler=AsyncMock(return_value=expected)
        self.assertIs(await cap.wrap_model_request(ctx,request_context=object(),handler=handler),expected)
        cap,ctx=self.setup_capability(self.completed());ctx.deps.turn_id='follow-up'
        handler=AsyncMock(return_value=expected)
        self.assertIs(await cap.wrap_model_request(ctx,request_context=object(),handler=handler),expected)
        cap,ctx=self.setup_capability(self.completed());ctx.run_id='child'
        handler=AsyncMock(return_value=expected)
        self.assertIs(await cap.wrap_model_request(ctx,request_context=object(),handler=handler),expected)

    async def test_legacy_workflow_replay_does_not_insert_completion_activity(self):
        cap,ctx=self.setup_capability(self.completed())
        expected=ModelResponse(parts=[TextPart('normal')]);handler=AsyncMock(return_value=expected)
        with patch('repository_workflow.workflow.in_workflow',return_value=True), \
                patch('repository_workflow.workflow.patched',side_effect=lambda name:name!='local-repository-completion-v1'):
            self.assertIs(await cap.wrap_model_request(ctx,request_context=object(),handler=handler),expected)

    async def test_legacy_completion_replay_does_not_insert_new_authority_hooks(self):
        cap,ctx=self.setup_capability(self.completed())
        cap.completion_message=AsyncMock(return_value='Previously recorded completion')
        handler=AsyncMock()
        with patch('repository_workflow.workflow.in_workflow',return_value=True), \
                patch('repository_workflow.workflow.patched',side_effect=lambda name:name!='local-repository-completion-authority-v1'):
            result=await cap.wrap_model_request(ctx,request_context=object(),handler=handler)
        self.assertEqual(result.parts[0].content,'Previously recorded completion')
        ctx.root_capability.before_model_request.assert_not_awaited()
        handler.assert_not_awaited()


if __name__=='__main__':
    unittest.main()
