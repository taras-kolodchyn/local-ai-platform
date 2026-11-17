"""Native Moyai repository tools and durable phase gates for attached conversations."""
import asyncio
from dataclasses import replace
from functools import cached_property
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai._run_context import set_current_run_context
from pydantic_ai.capabilities import AbstractCapability, durable_operation
from pydantic_ai.exceptions import ModelRetry, ToolFailed, ToolRetryError, UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.toolsets import FunctionToolset
from temporalio import workflow

from repository_backend import NativeFiles, RepositoryBackend, fingerprint, scoped_tree, workspace_path
from repository_ledger import ARTIFACT_PATHS, RepositoryError, missing_change_evidence

DISCOVERY = {'list_repositories', 'open_repository'}
FILES = {'read_file', 'list_files', 'grep'}
WRITES = {'edit_file', 'write_file'}
PLANNING = {'write_plan', 'read_plan', 'update_task_status', 'update_task_statuses'}
STATUS = 'repository_task_status'
CHECK = 'run_repository_checks'
BEGIN = 'begin_repository_changes'
FINISH = 'finish_repository_task'
SEARCH = 'search_repository'
REPOSITORY = DISCOVERY | {STATUS, CHECK, BEGIN, FINISH, SEARCH}


def allowed(phase):
    if phase is None:
        return None
    if phase == 'preparing':
        return {STATUS, 'open_repository'}
    if phase in {'analyzing','implementing','verified'}:
        names = FILES | PLANNING | {STATUS, SEARCH, CHECK}
        if phase == 'analyzing':
            return names | {BEGIN}
        return names | WRITES | ({FINISH} if phase == 'verified' else set())
    if phase == 'delivering':
        return {STATUS, FINISH}
    if phase == 'completed':
        return {STATUS, 'save_artifact'}
    return {STATUS}


def active_version():
    return not workflow.in_workflow() or workflow.patched('local-repository-workflow-v1')


def completion_authority_version():
    return not workflow.in_workflow() or workflow.patched('local-repository-completion-authority-v1')


def scope_of(ctx):
    return (ctx.deps.session_id, ctx.deps.workspace_key)


def identity(ctx, call_id):
    return fingerprint([ctx.deps.turn_id,ctx.run_id,call_id])


class RepositoryWorkflow(AbstractCapability):
    id = 'local_repository_workflow_v1'

    def __init__(self, backend=None):
        self.backend = backend or RepositoryBackend()

    @cached_property
    def registered_tools(self):
        backend = self.backend

        async def list_repositories(ctx: RunContext) -> list[dict]:
            """List the operator-registered repositories available for this local workflow."""
            return [{k:r[k] for k in ('repository_id','name','verification_profile')} for r in await asyncio.to_thread(backend.available)]

        async def open_repository(ctx: RunContext, repository_id: str) -> dict:
            """Attach the registered repository ID to this chat and pin its immutable snapshot."""
            return await backend.open(scope_of(ctx), repository_id, ctx.deps.actor_id)

        async def repository_task_status(ctx: RunContext) -> dict:
            """Inspect this task's phase, verification attempts and deliverable paths."""
            return await backend.status(scope_of(ctx), ctx.deps.turn_id)

        async def search_repository(ctx: RunContext, query: str, limit: int = 6) -> dict:
            """Retrieve cited passages from this conversation's pinned source generation."""
            return await backend.search(scope_of(ctx), query, limit)

        async def begin_repository_changes(ctx: RunContext) -> dict:
            """Allow native file edits after cited retrieval, a failing baseline and a native acceptance plan."""
            scope = scope_of(ctx)
            items = await backend.plans(scope[0]).get_items()
            try:
                backend.ledger.begin(*scope, [i.model_dump(mode='json') for i in items])
            except RepositoryError as error:
                raise ToolFailed(str(error)) from error
            return await backend.status(scope)

        async def run_repository_checks(ctx: RunContext) -> dict:
            """Run the fixed offline profile. Add regressions in tests/*.rs, follow next_action, and edit before retrying failed checks. An unchanged repair reuses settled evidence without another validation attempt."""
            return await backend.check(scope_of(ctx), identity(ctx,ctx.tool_call_id))

        async def finish_repository_task(ctx: RunContext) -> dict:
            """Deliver the patch and reports to native Files and return verified download links."""
            return await backend.finish(scope_of(ctx), ctx)

        return FunctionToolset(id='local_repository_tools_v1', sequential=True,
                               tools=[list_repositories, open_repository, search_repository, begin_repository_changes,
                                      run_repository_checks, finish_repository_task, repository_task_status])

    def get_toolset(self):
        return self.registered_tools

    @durable_operation('completion_message')
    async def completion_message(self, session: str, authority: str, turn: str) -> str | None:
        task=self.backend.ledger.status(session,authority)
        if (not task or task['phase']!='completed' or task.get('completion_turn')!=turn or task.get('pending')
                or not task.get('verified_tree') or not task.get('receipts')
                or task['receipts'][-1].get('state')!='passed'
                or task['receipts'][-1].get('tree_hash')!=task['verified_tree']
                or set(task.get('artifacts',{}))!=set(ARTIFACT_PATHS)
                or set(task.get('files',{}))!=set(ARTIFACT_PATHS)):
            return None
        files=task['files']
        if any(not files[p].get('id') or files[p].get('sha256')!=task['artifacts'][p] for p in ARTIFACT_PATHS):
            return None
        from urllib.parse import quote
        links='\n'.join(f"- [{p.split('/')[-1]}](/api/files/{quote(files[p]['id'],safe='')})" for p in ARTIFACT_PATHS)
        return 'Repository task completed. Trusted checks passed on the delivered tree.\n\n'+links

    async def wrap_model_request(self, ctx, *, request_context, handler):
        if (active_version() and ctx.run_id==ctx.deps.root_run_id
                and (not workflow.in_workflow() or workflow.patched('local-repository-completion-v1'))):
            if message:=await self.completion_message(*scope_of(ctx),ctx.deps.turn_id):
                if completion_authority_version():
                    if not workflow.in_workflow() or workflow.patched('local-repository-completion-native-authority-v2'):
                        # Only native authority/checkpoint work is needed for a synthetic
                        # response. Re-entering the full before-chain would invoke Temporal's
                        # model repair hook outside the durable model wrapper's scope.
                        if not workflow.in_workflow() or workflow.patched('local-repository-completion-authority-import-v3'):
                            # Moyai's import-time setup belongs to the worker process,
                            # just like its own workflow's native runtime imports.
                            with workflow.unsafe.imports_passed_through():
                                from moyai.agent import Authority
                        else:
                            from moyai.agent import Authority
                        authority=ctx.capabilities.get('moyai_authority')
                        if not isinstance(authority,Authority):
                            raise PermissionError('Native completion authority is unavailable.')
                        with set_current_run_context(ctx):
                            await authority.before_model_request(ctx,request_context)
                    else:
                        await ctx.root_capability.before_model_request(ctx,request_context)
                return ModelResponse(parts=[TextPart(message)],finish_reason='stop',provider_name='local-controller')
        return await handler(request_context)

    @durable_operation('phase')
    async def phase(self, session: str, authority: str) -> str | None:
        task = self.backend.ledger.status(session, authority)
        return task['phase'] if task else None

    @durable_operation('model_budget')
    async def model_budget(self, session: str, authority: str, operation: str) -> str | None:
        try:
            self.backend.ledger.charge(session, authority, 'model', operation)
        except RepositoryError as error:
            return str(error)
        return None

    async def before_model_request(self, ctx, request_context):
        if active_version():
            if (completion_authority_version() and ctx.run_id==ctx.deps.root_run_id
                    and await self.completion_message(*scope_of(ctx),ctx.deps.turn_id)):
                return request_context
            reason = await self.model_budget(*scope_of(ctx), identity(ctx, str(ctx.run_step)))
            if reason:
                raise UsageLimitExceeded(reason)
        return request_context

    async def prepare_tools(self, ctx, tool_defs):
        if not active_version():
            return [t for t in tool_defs if t.name not in REPOSITORY]
        phase = await self.phase(*scope_of(ctx))
        root = ctx.run_id == ctx.deps.root_run_id
        if phase is None:
            return [t for t in tool_defs if t.name not in REPOSITORY or (root and t.name in DISCOVERY)]
        names = allowed(phase) if root else {STATUS}
        return [replace(t, sequential=True) for t in tool_defs if t.name in names]

    @durable_operation('admit')
    async def admit(self, session: str, authority: str, root: bool, operation: str,
                    name: str, path: str, argument_hash: str, size: int, turn: str) -> dict[str, Any]:
        scope = (session, authority)
        ledger = self.backend.ledger
        try:
            task = ledger.status(*scope)
            if not task:
                if name in REPOSITORY and (not root or name not in DISCOVERY):
                    raise RepositoryError('Open a registered repository from the parent conversation first.')
                return {}
            ledger.charge(*scope, 'tool', operation)
            if (not root and name != STATUS) or name not in allowed(task['phase']):
                raise RepositoryError('Tool unavailable in repository phase ' + task['phase'] + '.')
            if name == 'save_artifact':
                if path not in task['artifacts']:
                    raise RepositoryError('Only controller-produced repository artifacts can be delivered.')
                return {'artifact_digest':task['artifacts'][path]}
            if name in FILES | WRITES:
                confined = workspace_path(name, {'path':path})
                if name in WRITES:
                    if size > 524288:
                        raise RepositoryError('Repository edit exceeds the file size limit.')
                    manifest, _ = await asyncio.to_thread(self.backend.snapshot, task['binding'])
                    _, before = scoped_tree(await self.backend.guest(scope, 'tree'), manifest)
                    ledger.start_write(*scope, operation, argument_hash, before, turn=turn,
                                       path=confined.removeprefix('repository/'))
                return {'path':confined}
            return {}
        except RepositoryError as error:
            return {'denied':str(error)}

    async def before_tool_execute(self, ctx, *, call, tool_def, args):
        if not active_version():
            if call.tool_name in REPOSITORY:
                raise ToolFailed('Repository tools are unavailable for this older execution.')
            return args
        outcome = await self.admit(*scope_of(ctx), ctx.run_id == ctx.deps.root_run_id,
                                   identity(ctx,call.tool_call_id), call.tool_name, args.get('path','.'),
                                   fingerprint([call.tool_name,call.args_as_dict()]) if call.tool_name in WRITES else '',
                                   len(str(args).encode()) if call.tool_name in WRITES else 0, ctx.deps.turn_id)
        if reason := outcome.get('denied'):
            raise ToolFailed(reason)
        if expected := outcome.get('artifact_digest'):
            if not await self.check_delivery_source(ctx, args['path'], expected):
                raise ToolFailed('Artifact bytes changed. Use the existing verified Files download.')
            return args
        return {**args, **outcome} if outcome else args

    @durable_operation('check_delivery_source')
    async def check_delivery_source(self, ctx: RunContext, path: str, digest: str) -> bool:
        try:
            await NativeFiles.verify_source(ctx, path, digest)
        except (ValueError, OSError):
            return False
        return True

    @durable_operation('write_settlement')
    async def write_settlement(self, session: str, authority: str, operation: str, outcome: str) -> None:
        ledger = self.backend.ledger
        task = ledger.status(session, authority)
        if task and task['pending'] == operation:
            # The native write activity records its own dispatch and verified postimage.
            # A reserved claim has not reached write_bytes; close it atomically to fence late delivery.
            if task.get('write_dispatch') == 'reserved':
                ledger.finish_reserved_write(session, authority, operation)
            elif outcome != 'completed' or task.get('write_dispatch') != 'dispatched':
                ledger.uncertain(session, authority, operation)

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        if not active_version() or tool_def.name not in WRITES:
            return await handler(args)
        operation = identity(ctx, call.tool_call_id)
        try:
            # Harness 0.55 runs file tools in the workflow and dispatches individual
            # WorkspaceCall activities. Give the native durable workspace this exact
            # tool context so its serialized write carries the admitted call identity.
            with set_current_run_context(ctx):
                result = await handler(args)
        except (ModelRetry, ToolRetryError):
            await self.write_settlement(*scope_of(ctx), operation, 'refused')
            raise
        except ToolFailed as error:
            # The native lazy workspace can deliberately defer the first file call.
            # Transport failures translated by the tool caller have an unknown effect.
            refused = str(error).startswith(('The computer is ready.', 'New guidance arrived before dispatch'))
            await self.write_settlement(*scope_of(ctx), operation, 'refused' if refused else 'unknown')
            raise
        except (Exception, asyncio.CancelledError):
            await self.write_settlement(*scope_of(ctx), operation, 'unknown')
            raise
        await self.write_settlement(*scope_of(ctx), operation, 'completed')
        return result

    @durable_operation('artifact_digest')
    async def artifact_digest(self, session: str, authority: str, path: str) -> str | None:
        task = self.backend.ledger.status(session, authority)
        return task['artifacts'].get(path, '') if task else None

    @durable_operation('plan_feedback')
    async def plan_feedback(self, session: str, authority: str, accepted: bool) -> str | None:
        task = self.backend.ledger.status(session, authority)
        if not task:
            return None
        if not accepted:
            return ('Retry write_plan with the corrected items. Use pending for future steps without '
                    'dependencies. A blocked step requires a nonempty depends_on.')
        if task['phase'] == 'analyzing':
            items = await self.backend.plans(session).get_items()
            missing = missing_change_evidence(task, [i.model_dump(mode='json') for i in items])
            return missing or 'The persisted plan, citations and baseline are ready. Call begin_repository_changes now.'
        return None

    async def after_tool_execute(self, ctx, *, call, tool_def, args, result):
        if (tool_def.name == 'write_plan' and active_version()
                and (not workflow.in_workflow() or workflow.patched('local-repository-plan-feedback-v1'))):
            accepted = isinstance(result, str) and result.startswith(('Plan updated.', 'Plan updated:'))
            guidance = await self.plan_feedback(*scope_of(ctx), accepted)
            if guidance and not accepted:
                raise ToolFailed(str(result)[:1000] + ' ' + guidance)
            if guidance:
                return result + '\n\n' + guidance
        if active_version() and tool_def.name == 'save_artifact':
            expected = await self.artifact_digest(*scope_of(ctx), args['path'])
            if expected is not None and (not expected or not isinstance(result, dict)
                                         or result.get('sha256') != expected):
                raise ToolFailed('Downloaded file digest does not match the controller-produced artifact.')
        return result
