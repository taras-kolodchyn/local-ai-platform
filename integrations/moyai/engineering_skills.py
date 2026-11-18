"""Select local engineering skills for a chat turn and load them through the SDK."""
from dataclasses import replace
import json
from pathlib import Path
import re

from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ModelRequest, RetryPromptPart, ToolReturnPart, UserPromptPart
from temporalio import workflow

SELECT_SKILLS = 'select_engineering_skills'
LOAD = 'load_capability'
RETRY = 'Use the native load_capability tool now for this skill ID: '
QWEN_CALL = re.compile(
    r'(?:^|\n)[ \t]*(?:<tool_call>\s*)?<function=load_capability>\s*'
    r'<parameter=id>\s*([a-z0-9-]+)\s*</parameter>\s*</function>\s*(?:</tool_call>)?\s*\Z'
)


def selected_ids(messages, run_id):
    for message in reversed(messages):
        if message.run_id == run_id:
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == SELECT_SKILLS and part.outcome == 'success':
                    return part.content['skills']
    return None


def force_call(request, tool, prompt, field=None, constraint=None):
    # Keep full conversation history in the SDK. Re-sending the malformed XML
    # or other tool schemas to Qwen on this wire request can repeat the error.
    if field is not None:
        schema = tool.parameters_json_schema
        properties = {**schema['properties'], field: {**schema['properties'][field], **constraint}}
        tool = replace(tool, parameters_json_schema={**schema, 'properties': properties})
    params = replace(request.model_request_parameters, function_tools=[tool], instruction_parts=[],
                     native_tools=[], output_tools=[])
    return replace(request, messages=[ModelRequest(parts=[UserPromptPart(prompt)])],
                   model_request_parameters=params,
                   model_settings={**(request.model_settings or {}), 'tool_choice': [tool.name]})


class EngineeringSkills:
    def __init__(self):
        manifest = json.loads((Path(__file__).with_name('skills') / 'catalog.json').read_text())
        self.catalog = {row['name']: row['description'] for row in manifest['skills']}

    def select(self, skills: list[str]) -> dict:
        """Select the local engineering workflows applicable to this request. Return an empty list if none apply."""
        if any(name not in self.catalog for name in skills) or len(skills) != len(set(skills)):
            raise ModelRetry('Choose distinct IDs from the offered engineering skills.')
        return {'skills': skills}

    def needs_selection(self, ctx, messages):
        return (ctx.run_id == ctx.deps.root_run_id and getattr(ctx.deps, 'surface', None) == 'web'
                and isinstance(ctx.prompt, str)
                and not re.search(r'\b(?:repository_id|open_repository)\b', ctx.prompt)
                and any(name not in ctx.active_capability_ids for name in self.catalog)
                and (not workflow.in_workflow() or workflow.patched('local-engineering-skill-selection-v1'))
                and selected_ids(messages, ctx.run_id) is None)

    def prepare(self, ctx, request):
        params = request.model_request_parameters
        tools = {t.name: t for t in params.function_tools}
        choice = (request.model_settings or {}).get('tool_choice')
        # Respect caller-controlled restrictions. Ordinary Moyai uses automatic choice.
        if choice not in (None, 'auto', 'required') or LOAD not in tools or tools[LOAD].tool_kind != 'capability-load':
            return request
        available = [name for name in self.catalog if name in params.deferred_capability_ids
                     and name not in ctx.active_capability_ids]
        if not available:
            return request
        last = request.messages[-1].parts if request.messages else []
        if last and isinstance(last[-1], RetryPromptPart) and not last[-1].tool_name:
            error = last[-1].content
            if isinstance(error, str) and error.startswith(RETRY) and error[len(RETRY):] in available:
                name = error[len(RETRY):]
                return force_call(request, tools[LOAD], f'Load the skill {name} now.', 'id', {'enum': [name]})
        if SELECT_SKILLS in tools and self.needs_selection(ctx, request.messages):
            catalog = '\n'.join(f'{name}: {self.catalog[name]}' for name in available)
            prompt = ('You are a sparse intent classifier. Match the verb and object of each explicit request '
                      'to the primary output of exactly one catalog entry. Constraints, examples, criteria and '
                      'details of that output do not count as additional requests. Return one ID per distinct '
                      'match, in order. If none match, return an empty array. Select a further ID only if you '
                      'can point to a separate requested artifact or action that every selected entry misses. '
                      'When the user expressly names a workflow, include it.\n\n'
                      f'Catalog:\n{catalog}\n\nRequest:\n{ctx.prompt}')
            # Qwen can copy IDs from a second catalog in the tool schema. Check the
            # model's choices against the offered capabilities when the call returns.
            return force_call(request, tools[SELECT_SKILLS], prompt)
        for name in selected_ids(request.messages, ctx.run_id) or ():
            if name in available:
                return force_call(request, tools[LOAD], f'Load the skill {name} now.', 'id', {'enum': [name]})
        return request

    def validate(self, ctx, request, response):
        tools = request.model_request_parameters.function_tools
        choice = (request.model_settings or {}).get('tool_choice')
        calls = response.tool_calls
        if len(tools) == 1 and choice == [tools[0].name]:
            if tools[0].name == SELECT_SKILLS:
                if len(calls) != 1 or calls[0].tool_name != SELECT_SKILLS:
                    raise ModelRetry('Return the requested selection using the native selection tool.')
                offered = request.model_request_parameters.deferred_capability_ids.intersection(self.catalog)
                skills = calls[0].args_as_dict().get('skills')
                if (not isinstance(skills, list) or any(not isinstance(name, str) or name not in offered
                        or name in ctx.active_capability_ids for name in skills)):
                    raise ModelRetry('Select only IDs from the offered engineering workflows.')
            if tools[0].name == LOAD and tools[0].tool_kind == 'capability-load':
                ids = tools[0].parameters_json_schema['properties']['id'].get('enum', [])
                if len(ids) == 1 and (len(calls) != 1 or calls[0].tool_name != LOAD
                                      or calls[0].args_as_dict() != {'id': ids[0]}):
                    raise ModelRetry(RETRY + ids[0])
        if not calls and response.text:
            match = QWEN_CALL.search(response.text)
            if (match and response.text[:match.start()].count('```') % 2 == 0 and match[1] in self.catalog
                    and match[1] in request.model_request_parameters.deferred_capability_ids
                    and match[1] not in ctx.active_capability_ids and any(t.tool_kind == 'capability-load' for t in tools)
                    and choice in (None, 'auto', 'required')
                    and (not workflow.in_workflow() or workflow.patched('local-native-skill-retry-v1'))):
                raise ModelRetry(RETRY + match[1])
        return response
