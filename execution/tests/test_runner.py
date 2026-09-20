import asyncio
from pathlib import Path
import sys

from workspace_runtime.runner import Runner


def test_timeout_reaps_process_and_next_task_succeeds(tmp_path):
    async def check():
        runner = Runner(tmp_path, [sys.executable, '-c', 'import time; time.sleep(20)'], timeout=0.1)
        result = await runner.run('one', 'hello')
        assert result['state'] == 'failed'
        runner.command = [sys.executable, '-c', 'import json; print(json.dumps({"text":"ready","memory_status":"ready"}))']
        result = await runner.run('two', 'hello')
        assert result['state'] == 'completed'
        assert result['text'] == 'ready'
        assert (await runner.run('two', 'changed')) == result
    asyncio.run(check())


def test_output_bound_and_interrupted_state(tmp_path):
    async def check():
        runner = Runner(tmp_path, [sys.executable, '-c', 'print("x"*300000)'], timeout=3)
        result = await runner.run('large', 'hello')
        assert result['state'] == 'failed'
        runner.save('old', {'state': 'working', 'text': '', 'memory_status': 'unknown'})
        restarted = Runner(tmp_path, [sys.executable, '-c', 'raise RuntimeError()'])
        assert restarted.get('old')['state'] == 'failed'
    asyncio.run(check())


def test_cancel_releases_slot(tmp_path):
    async def check():
        runner = Runner(tmp_path, [sys.executable, '-c', 'import time; time.sleep(20)'])
        task = asyncio.create_task(runner.run('cancel', 'hello'))
        for _ in range(100):
            if runner.active: break
            await asyncio.sleep(0.01)
        assert await runner.cancel('cancel')
        assert (await task)['state'] == 'canceled'
        assert not runner.active
    asyncio.run(check())


def test_deadline_includes_stdin_backpressure(tmp_path):
    async def check():
        runner = Runner(tmp_path, [sys.executable, '-c', 'import time; time.sleep(20)'], timeout=0.1)
        result = await asyncio.wait_for(runner.run('blocked-input', '\U0001f680' * 16000), timeout=1)
        assert result['state'] == 'failed'
        assert result['text'] == 'Execution deadline exceeded'
    asyncio.run(check())
