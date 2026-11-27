"""Evidence validation for the local acceptance harness, independent of model claims."""
import json


def valid_pytest_action(action):
    try:
        command = json.loads(action['input'])['command'].strip()
        lines = action['output'].strip().splitlines()
        status = json.loads(lines[-1])
        return (command in ('python -m pytest -q', 'python3 -m pytest -q')
                and isinstance(status, dict) and type(status.get('exit_code')) is int
                and status['exit_code'] == 0 and any('3 passed' in line for line in lines[:-1]))
    except (KeyError, IndexError, ValueError, TypeError, AttributeError):
        return False


def valid_workspace_states(first, continued, other, expected_hashes):
    return (all(isinstance(state.get('uid'), int) and state['uid'] > 200000 for state in (first, continued, other))
            and first['uid'] == continued['uid'] and first['uid'] != other['uid']
            and first.get('directory') == continued.get('directory')
            and other.get('directory') != first.get('directory')
            and first.get('files') == continued.get('files') == expected_hashes
            and other.get('files') == {name: None for name in expected_hashes})
