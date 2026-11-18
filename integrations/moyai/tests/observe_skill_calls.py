"""Compare native tool returns from one test turn with the packaged instructions."""
import json
from pathlib import Path
import sqlite3
import sys

from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, ToolCallPart, ToolReturnPart


def observe(turn):
    with sqlite3.connect('file:/data/agent-state.db?mode=ro', uri=True) as db:
        row = db.execute("SELECT messages FROM snapshots WHERE run_id=? AND state='complete' "
                         "ORDER BY seq DESC LIMIT 1", (turn,)).fetchone()
    if row is None:
        raise RuntimeError('No completed native snapshot for the test turn')
    root = Path('/opt/moyai-local/skills')
    expected = {}
    for skill in json.loads((root / 'catalog.json').read_text())['skills']:
        name = skill['name']
        body = (root / name / 'SKILL.md').read_text().split('---\n', 2)[2].strip()
        expected[name] = f'# Skill: {name}\n\n{body}'
    calls = {}
    verified = {}
    selections = {}
    accepted = {}
    for message in ModelMessagesTypeAdapter.validate_json(row[0]):
        if message.run_id != turn:
            continue
        for part in message.parts:
            if (isinstance(part, ToolCallPart) and isinstance(message, ModelResponse)
                    and (message.provider_name, message.model_name) == ('litellm', 'local-qwen')):
                if part.tool_name == 'select_engineering_skills':
                    selections[part.tool_call_id] = part.args_as_dict().get('skills')
                elif part.tool_name == 'load_capability':
                    name = part.args_as_dict().get('id')
                    if name in expected:
                        calls[part.tool_call_id] = name
            if (isinstance(part, ToolReturnPart) and part.tool_name == 'select_engineering_skills'
                    and part.outcome == 'success' and part.tool_call_id in selections
                    and part.content == {'skills': selections[part.tool_call_id]}):
                accepted[part.tool_call_id] = selections[part.tool_call_id]
            if isinstance(part, ToolReturnPart) and part.tool_name == 'load_capability' and part.tool_call_id in calls:
                name = calls[part.tool_call_id]
                if (part.outcome == 'success' and isinstance(part.content, dict)
                        and part.content.get('instructions', '').strip() == expected[name]):
                    verified[name] = part.tool_call_id
    if len(accepted) != 1:
        raise RuntimeError('Expected one successful selection from local Qwen in this fresh turn')
    selection_id, selected = next(iter(accepted.items()))
    return {'selection_id': selection_id, 'selected_skills': selected, 'verified_skill_calls': verified}


if __name__ == '__main__':
    print(json.dumps(observe(sys.argv[1])))
