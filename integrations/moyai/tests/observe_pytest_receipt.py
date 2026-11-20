"""Read original shell receipts for a smoke turn after SDK history compaction."""
from contextlib import closing
import json
import sqlite3
import sys

from pydantic_ai.messages import ModelMessagesTypeAdapter, ToolCallPart, ToolReturnPart

from acceptance import valid_pytest_action


def pytest_receipt(messages, turn, action_ids):
    calls = {}
    for message in messages:
        if message.run_id != turn:
            continue
        for part in message.parts:
            if not isinstance(part, (ToolCallPart, ToolReturnPart)) or part.tool_name != 'shell':
                continue
            action = part.tool_call_id
            if action not in action_ids:
                continue
            if isinstance(part, ToolCallPart):
                calls[action] = part.args_as_dict()
            elif part.outcome == 'success' and action in calls:
                if valid_pytest_action({'input': json.dumps(calls[action]), 'output': part.content}):
                    return action
    return None


def observe(turn, action_ids):
    with closing(sqlite3.connect('file:/data/agent-state.db?mode=ro', uri=True)) as db:
        rows = db.execute("SELECT messages FROM snapshots WHERE run_id=? AND state='complete' ORDER BY seq", (turn,))
        for (raw,) in rows:
            receipt = pytest_receipt(ModelMessagesTypeAdapter.validate_json(raw), turn, action_ids)
            if receipt:
                return receipt
    return None


if __name__ == '__main__':
    print(json.dumps(observe(sys.argv[1], set(sys.argv[2:]))))
