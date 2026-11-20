"""The paired benchmark only accepts citations to evidence actually supplied."""
import importlib.util
import json
from pathlib import Path

path = Path(__file__).resolve().parents[2] / 'scripts/benchmark-context.py'
spec = importlib.util.spec_from_file_location('context_benchmark', path)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_grade_requires_all_facts_and_actual_cited_lines():
    files = {'a.sh': ['ignored', 'call helper', 'irrelevant', 'set mode'],
             'helper.sh': ['limit=2']}
    rows = [{'path': 'a.sh', 'start_line': 2, 'end_line': 4, 'content': 'call helper\n…\nset mode'},
            {'path': 'helper.sh', 'start_line': 1, 'end_line': 1, 'content': 'limit=2'}]
    _, shown = benchmark.render(rows, files)
    assert set(shown) == {'a.sh:2', 'a.sh:4', 'helper.sh:1'}
    def grade(answers, citations):
        return benchmark.grade(json.dumps(dict(answers=answers, citations=citations)), shown,
                               ['helper', '2'], [('a.sh', 'call helper'), ('helper.sh', 'limit=2')])
    assert grade(['helper', 2], ['a.sh:2', 'helper.sh:1']) == (True, True)
    assert grade(['helper', 3], ['a.sh:2', 'helper.sh:1']) == (False, True)
    assert grade(['helper', 2], ['a.sh:4', 'helper.sh:1']) == (True, False)
    assert grade(['helper', 2], ['a.sh:3', 'helper.sh:1']) == (True, False)
