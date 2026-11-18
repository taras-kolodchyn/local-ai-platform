"""The local analysis profile is part of Moyai's persisted, server-owned turn scope."""
from dataclasses import replace
from functools import wraps
import re

from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import InstructionPart, TextPart

from engineering_skills import LOAD, SELECT_SKILLS, selected_ids

PROFILE = 'local-qwen-analysis'
TOOLS = frozenset({SELECT_SKILLS, LOAD, 'read_file', 'list_files'})
GUIDANCE = ('Analysis only. Deliver the requested plan, proposed code and decisions directly in your final chat '
            'answer. Use the supplied code when it is sufficient. You can inspect existing files and load the '
            'offered skills. If asked to change files, explain that this profile leaves them untouched. '
            'Never simulate an operation with XML. Describe suggested checks as proposals unless you have observed their results.')
TRAILING_CALL = re.compile(r'(?:^|\n)[ \t]*(?:<tool_call>\s*)?<function=([\w-]+)>'
                           r'(?:(?!</function>).)*</function>\s*(?:</tool_call>)?\s*\Z', re.S)

# Deliberate user directives at the beginning or end of ordinary chat prose.
# The model picker is the language-independent, explicit control.
START = re.compile(r'^\s*(?:/analysis(?=\s|$)|(?:тільки аналіз|лише аналіз|analysis only)(?=[.!:\n]|$))', re.I)
END = re.compile(r'(?:^|[\n,.!;]\s*)(?:без змін у файлах|без змін у файлах пісочниці|'
                 r'без запису у файли|no file changes)\s*[.!]?\s*$', re.I)


def install_analysis_routing(database):
    submit = database.submit

    @wraps(submit)
    async def submit_with_profile(actor, session_id, body, **kwargs):
        if (body.source['kind'] == 'web' and body.reply_to is None and body.model in (None, 'local-qwen')
                and not any(kwargs.get(k) for k in ('swarm_generated', 'automation_generated', 'delegated_authority'))
                and (START.search(body.authored_text) or END.search(body.authored_text))):
            body = body.model_copy(update={'model': PROFILE})
        return await submit(actor, session_id, body, **kwargs)

    database.submit = submit_with_profile


def active(ctx):
    return getattr(ctx.deps, 'model', None) == PROFILE


def chat_response(ctx, response):
    # A local provider can append a simulated write to an otherwise complete
    # answer even on a text-only request. Keep literal examples in code fences.
    if response.tool_calls or not response.parts or not isinstance(response.parts[-1], TextPart):
        return response
    part = response.parts[-1]
    match = TRAILING_CALL.search(part.content)
    if not match or match[1] in TOOLS or part.content[:match.start()].count('```') % 2:
        return response
    body = part.content[:match.start()].rstrip()
    if not body and not any(isinstance(p, TextPart) and p.content.strip() for p in response.parts[:-1]):
        raise ModelRetry('Answer the original task directly in chat, including proposed code inline. File changes are disabled.')
    note = ('У режимі «тільки аналіз» файли не змінювалися.' if isinstance(ctx.prompt, str)
            and re.search('[А-Яа-яІіЇїЄєҐґ]', ctx.prompt) else 'Analysis only. No files were changed.')
    return replace(response, parts=[*response.parts[:-1], replace(part, content=(body + '\n\n' + note).strip())])


def restrict_request(ctx, request, catalog):
    # The SDK adds deferred loaders after prepare_tools. Apply the same policy
    # to the final model request, including provider-native tools.
    params = request.model_request_parameters
    ids = params.deferred_capability_ids.intersection(catalog).difference(ctx.active_capability_ids)
    selected = selected_ids(request.messages, ctx.run_id)
    if selected is not None:
        ids.intersection_update(selected)
    offered = []
    for tool in params.function_tools:
        if tool.name not in TOOLS:
            continue
        if tool.name == LOAD:
            if tool.tool_kind != 'capability-load' or not ids:
                continue
            schema = tool.parameters_json_schema
            prop = schema['properties']['id']
            allowed = sorted(ids.intersection(prop.get('enum', ids)))
            if not allowed:
                continue
            tool = replace(tool, parameters_json_schema={**schema, 'properties': {
                **schema['properties'], 'id': {**prop, 'enum': allowed}}})
        offered.append(tool)
    guidance = GUIDANCE if offered else (GUIDANCE + ' All available context is already in this conversation. '
                                        'Give the complete answer now using the language requested by the user.')
    return replace(request, model_request_parameters=replace(
        params, function_tools=offered, deferred_capability_ids=ids, native_tools=[],
        instruction_parts=[*(params.instruction_parts or []), InstructionPart(guidance)]))
