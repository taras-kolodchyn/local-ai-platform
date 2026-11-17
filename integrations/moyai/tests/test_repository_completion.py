"""A verified Files delivery ends the native turn without another inference call."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.workspaces import LocalWorkspaceBackend, Workspace
from moyai.agent import Authority
from repository_ledger import ARTIFACT_PATHS
from repository_workflow import RepositoryWorkflow


class RepositoryCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_completion_preserves_native_authority_without_entering_inner_lifecycle(self):
        import tempfile
        from pathlib import Path

        class InnerLifecycle(AbstractCapability):
            async def before_model_request(self, ctx, request_context):
                # Like TemporalDurability, this hook requires its wrapper's scope.
                raise AssertionError('Inner model lifecycle was entered outside its wrapper')

        with tempfile.TemporaryDirectory() as directory:
            database=SimpleNamespace(authorize_scope=AsyncMock(),save_workspace_reference=AsyncMock())
            authority=Authority(database)
            class LocalComputer(AbstractCapability):
                def get_workspace(self, ctx, *, ref):
                    return Workspace(LocalWorkspaceBackend(Path(directory)))

            async def unexpected_model(*args):
                raise AssertionError('Verified completion called inference')

            for denied in (False,True):
                with self.subTest(denied=denied):
                    database.authorize_scope.reset_mock()
                    database.save_workspace_reference.reset_mock()
                    database.authorize_scope.side_effect=PermissionError('Revoked') if denied else None
                    cap,ctx=self.setup_capability(self.completed())
                    ctx.deps.actor_id='actor'
                    agent=Agent(FunctionModel(unexpected_model),capabilities=[LocalComputer(),cap,authority,InnerLifecycle()])
                    async def stream_result():
                        async with agent.run_stream('Finish verified delivery',deps=ctx.deps,run_id='root') as result:
                            return await result.get_output()
                    if denied:
                        with self.assertRaises(PermissionError):
                            await stream_result()
                        database.save_workspace_reference.assert_not_awaited()
                    else:
                        self.assertIn('Trusted checks passed',await stream_result())
                        database.save_workspace_reference.assert_awaited_once()
                    database.authorize_scope.assert_awaited_once()

    def setup_capability(self, task):
        ledger=SimpleNamespace(status=lambda session,authority:task)
        cap=RepositoryWorkflow(SimpleNamespace(ledger=ledger))
        authority=Authority(SimpleNamespace())
        authority.before_model_request=AsyncMock()
        ctx=SimpleNamespace(deps=SimpleNamespace(session_id='s',workspace_key='a',root_run_id='root',turn_id='turn'),
                            run_id='root',root_capability=SimpleNamespace(before_model_request=AsyncMock()),
                            capabilities={'moyai_authority':authority},_durable_operations=None)
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
        ctx.capabilities['moyai_authority'].before_model_request.side_effect=native_before
        handler=AsyncMock(return_value=ModelResponse(parts=[TextPart('unexpected model call')]))
        result=await cap.wrap_model_request(ctx,request_context=object(),handler=handler)
        handler.assert_not_awaited()
        ctx.capabilities['moyai_authority'].before_model_request.assert_awaited_once()
        ctx.root_capability.before_model_request.assert_not_awaited()
        cap.model_budget.assert_not_awaited()
        self.assertEqual(result.provider_name,'local-controller')
        self.assertTrue(all(isinstance(p,TextPart) for p in result.parts))
        self.assertIn('Trusted checks passed',result.parts[0].content)
        for i,path in enumerate(ARTIFACT_PATHS):
            self.assertIn(path.split('/')[-1],result.parts[0].content)
            self.assertIn('/api/files/file-'+str(i),result.parts[0].content)

    async def test_native_authority_denial_blocks_completion_response(self):
        cap,ctx=self.setup_capability(self.completed())
        ctx.capabilities['moyai_authority'].before_model_request.side_effect=PermissionError('Native authority revoked')
        handler=AsyncMock()
        with self.assertRaises(PermissionError):
            await cap.wrap_model_request(ctx,request_context=object(),handler=handler)
        handler.assert_not_awaited()

    async def test_missing_native_authority_fails_closed(self):
        cap,ctx=self.setup_capability(self.completed())
        ctx.capabilities={}
        handler=AsyncMock()
        with self.assertRaisesRegex(PermissionError,'authority is unavailable'):
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

    async def test_legacy_authority_replay_preserves_original_hook_path(self):
        cap,ctx=self.setup_capability(self.completed())
        cap.completion_message=AsyncMock(return_value='Recorded completion')
        handler=AsyncMock()
        with patch('repository_workflow.workflow.in_workflow',return_value=True), \
                patch('repository_workflow.workflow.patched',side_effect=lambda name:name!='local-repository-completion-native-authority-v2'):
            result=await cap.wrap_model_request(ctx,request_context=object(),handler=handler)
        self.assertEqual(result.parts[0].content,'Recorded completion')
        ctx.root_capability.before_model_request.assert_awaited_once()
        ctx.capabilities['moyai_authority'].before_model_request.assert_not_awaited()
        handler.assert_not_awaited()


if __name__=='__main__':
    unittest.main()
