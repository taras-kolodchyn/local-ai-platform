"""Verify task-specific skill selection by local Qwen in a fresh read-only chat."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integrations/moyai'))
from client import MoyaiClient


PROMPT = '''Маємо таку функцію Rust:

    fn display_width(label: &str) -> usize {
        if label.is_empty() { return 0; }
        label.chars().count()
    }

Сплануй рефакторинг із критеріями приймання, запропонуй простішу реалізацію з
тією самою поведінкою й напиши короткий ADR про вибір одиниці виміру для Unicode.
Увесь код наведено вище. Відповідай українською в чаті, без змін у файлах.'''

RUST_SKILLS = {'planning-and-task-breakdown', 'code-simplification', 'documentation-and-adrs'}
LATENCY_PROMPT = '''Для локального пошуку за однакових 40 запитів/с виміряли p95 до зміни 240 мс,
після зміни 120 мс. Порахуй виграш і поясни, як повторити порівняння, щоб воно було чесним.
Які метрики додати на дашборд і за якої умови має спрацювати сповіщення, якщо затримка повернеться?
Дай коротку відповідь українською в чаті, без змін у файлах.'''
LATENCY_SKILLS = {'performance-optimization', 'observability-and-instrumentation'}
ARITHMETIC_PROMPT = 'Скільки буде 18 + 26? Відповідай коротко в чаті, без змін у файлах.'


def workspace_state(session):
    return json.loads(subprocess.check_output([
        'docker', 'compose', '-f', 'compose.moyai.yaml', 'exec', '-T', '--user', 'workspace', 'moyai',
        'python', '/opt/moyai-local/tests/observe_workspace.py', '--analysis', session], cwd=ROOT))


def run(prompt=PROMPT, *, model='local-qwen', expected=RUST_SKILLS, title='Rust — план зміни, спрощення коду й ADR'):
    client = MoyaiClient()
    client.login()
    session = client.session(title, model=model)
    print(f'http://127.0.0.1:8787/#{session}', flush=True)
    before = workspace_state(session)
    if before['entries']:
        raise RuntimeError('Expected a fresh empty sandbox for the analysis smoke.')
    turn, detail = client.turn(session, prompt)
    if len(detail['turns']) != 1 or sum(m['role'] == 'user' for m in detail['messages']) != 1:
        raise RuntimeError('Acceptance requires one user message in a new chat.')
    if detail['turns'][0]['model'] != 'local-qwen-analysis':
        raise RuntimeError('The submitted chat directive did not select the analysis-only profile.')
    answer = '\n'.join(m['content'] for m in detail['messages']
                       if m['turn_id'] == turn and m['role'] == 'assistant')
    progress = client.request(f'/api/sessions/{session}/progress/{turn}?include_activity=true')
    visible = [answer, *(m['text'] for m in progress['messages'])]
    if not answer.strip() or any('<function=' in text or '<tool_call>' in text for text in visible):
        raise RuntimeError('The turn needs a final answer and visible activity free of tool-call markup.')
    if prompt == PROMPT and not all(part in answer.casefold() for part in ('план', 'критер', 'chars().count()', 'adr')):
        raise RuntimeError('The final chat answer must include the plan, acceptance criteria, Rust change and ADR.')
    if prompt == ARITHMETIC_PROMPT and not re.search(r'\b44\b', answer):
        raise RuntimeError('The final chat answer must give the sum of 18 and 26.')
    if prompt == LATENCY_PROMPT:
        reply = answer.casefold()
        if ('p95' not in reply or not any(term in reply.replace(' ', '') for term in ('50%', 'вдвічі', 'удвічі', '2рази', '2×', '2x'))
                or not any(term in reply for term in ('сповіщ', 'алерт', 'поріг', 'порог', 'сигнал', 'тривог'))):
            raise RuntimeError('The final chat answer must interpret the p95 change and address alerting.')
    receipt = json.loads(subprocess.check_output([
        'docker', 'compose', '-f', 'compose.moyai.yaml', 'exec', '-T', '--user', 'workspace', 'moyai',
        'python', '/opt/moyai-local/tests/observe_skill_calls.py', turn], cwd=ROOT))
    verified = receipt['verified_skill_calls']
    if set(receipt['selected_skills']) != expected or set(verified) != expected:
        raise RuntimeError(f"Expected {sorted(expected)}, selected {receipt['selected_skills']}, loaded {sorted(verified)}")
    selectors = {row['id'] for row in progress['tools']
                 if row['name'] == 'select_engineering_skills' and row['status'] == 'completed'}
    if selectors != {receipt['selection_id']}:
        raise RuntimeError('The model selection does not match the completed action in Moyai.')
    completed = {row['id'] for row in progress['tools']
                 if row['name'] == 'load_capability' and row['status'] == 'completed'}
    if set(verified.values()) != completed:
        raise RuntimeError('The native skill receipts do not match completed actions in Moyai.')
    if any(t['name'] not in {'load_capability', 'select_engineering_skills', 'read_file', 'list_files'}
           for t in progress['tools']):
        raise RuntimeError('An unexpected tool was dispatched in the analysis-only chat.')
    after = workspace_state(session)
    if after != before or client.request(f'/api/sessions/{session}/files'):
        raise RuntimeError('Analysis changed the sandbox or created an artifact.')
    report = {'session': session, 'turn': turn, **receipt, 'sandbox_before': before,
              'sandbox_after': after}
    print(json.dumps(report, indent=2), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=('rust', 'latency', 'arithmetic'), default='rust')
    case = parser.parse_args().case
    if case == 'latency':
        run(LATENCY_PROMPT, expected=LATENCY_SKILLS, title='Локальний пошук — затримка та сповіщення')
    elif case == 'arithmetic':
        run(ARITHMETIC_PROMPT, expected=set(), title='Просте обчислення')
    else:
        run()
