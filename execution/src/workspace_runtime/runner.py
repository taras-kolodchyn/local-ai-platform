"""Bounded subprocess execution with persistent task state and a shared GPU lock."""
import asyncio
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import time


class Runner:
    def __init__(self, home: Path, command: list[str], timeout: float = 45,
                 lock_dir: Path | None = None):
        self.home, self.command, self.timeout = home, command, timeout
        home.mkdir(parents=True, exist_ok=True)
        self.database = home / 'tasks.db'
        self.lock_path = (lock_dir or home) / 'generation.lock'
        self.active = {}
        self.pending = {}
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, result TEXT NOT NULL)')
            for tid, raw in db.execute('SELECT id, result FROM tasks').fetchall():
                if json.loads(raw)['state'] in ('working', 'submitted'):
                    db.execute('UPDATE tasks SET result=? WHERE id=?',
                        (json.dumps({'state': 'failed', 'text': 'Interrupted by restart',
                                     'memory_status': 'unknown'}), tid))

    def get(self, task_id):
        with sqlite3.connect(self.database) as db:
            row = db.execute('SELECT result FROM tasks WHERE id=?', (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, task_id, result):
        with sqlite3.connect(self.database) as db:
            db.execute('INSERT INTO tasks VALUES (?,?) ON CONFLICT(id) DO UPDATE SET result=excluded.result',
                       (task_id, json.dumps(result)))
        return result

    async def cancel(self, task_id):
        event = self.pending.get(task_id)
        if event is None:
            return False
        event.set()
        return True

    async def run(self, task_id, text):
        previous = self.get(task_id)
        if previous:
            return previous
        if len(self.pending) >= 4:
            return {'state': 'rejected', 'text': 'Execution queue is full', 'memory_status': 'unknown'}
        canceled = asyncio.Event()
        self.pending[task_id] = canceled
        self.save(task_id, {'state': 'submitted', 'text': '', 'memory_status': 'unknown'})
        process = None
        started = time.monotonic()
        result = {'state': 'failed', 'text': 'Execution failed', 'memory_status': 'unknown'}
        with self.lock_path.open('a') as lock:
            acquired = False
            try:
                while not acquired:
                    if canceled.is_set():
                        raise asyncio.CancelledError()
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                    except BlockingIOError:
                        if time.monotonic() - started >= 10:
                            raise TimeoutError('queue')
                        await asyncio.sleep(0.05)
                self.save(task_id, {'state': 'working', 'text': '', 'memory_status': 'unknown'})
                process = await asyncio.create_subprocess_exec(*self.command,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
                self.active[task_id] = process
                async def collect():
                    process.stdin.write(json.dumps({'text': text, 'task_id': task_id}).encode())
                    await process.stdin.drain()
                    process.stdin.close()
                    output = bytearray()
                    while chunk := await process.stdout.read(8192):
                        output.extend(chunk)
                        if len(output) > 262144:
                            raise ValueError('Output limit exceeded')
                    await process.wait()
                    return output

                collector = asyncio.create_task(collect())
                cancel_wait = asyncio.create_task(canceled.wait())
                try:
                    done, _ = await asyncio.wait([collector, cancel_wait], timeout=self.timeout,
                                                return_when=asyncio.FIRST_COMPLETED)
                    if cancel_wait in done:
                        raise asyncio.CancelledError()
                    if collector not in done:
                        raise TimeoutError('execution')
                    output = await collector
                    if process.returncode != 0:
                        raise ValueError('Child failed')
                    data = json.loads(output)
                    if not isinstance(data.get('text'), str) or len(data['text']) > 24000:
                        raise ValueError('Invalid response')
                    result = {'state': 'completed', 'text': data['text'],
                              'memory_status': data.get('memory_status', 'unknown')}
                finally:
                    for task in (collector, cancel_wait):
                        task.cancel()
                    await asyncio.gather(collector, cancel_wait, return_exceptions=True)
            except asyncio.CancelledError:
                result = {'state': 'canceled', 'text': 'Execution canceled', 'memory_status': 'unknown'}
            except TimeoutError:
                result['text'] = 'Execution deadline exceeded'
            except Exception:
                pass
            finally:
                if process is not None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()
                self.active.pop(task_id, None)
                self.pending.pop(task_id, None)
                if acquired:
                    fcntl.flock(lock, fcntl.LOCK_UN)
        return self.save(task_id, result)
