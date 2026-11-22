"""Time Compose progress events and report containers with outstanding operations."""
import json
from queue import Empty, Queue
import sys
from threading import Thread
from time import monotonic


def read_progress(lines):
    try:
        for line in sys.stdin:
            lines.put(line)
    finally:
        lines.put(None)


def report(label):
    start = monotonic()
    pending = {}
    lines = Queue()
    Thread(target=read_progress, args=(lines,), daemon=True).start()

    def emit(message):
        print(f'[timings] {label} Compose +{monotonic() - start:.3f}s {message}', flush=True)

    def outstanding():
        now = monotonic()
        return ', '.join(f'{name} {action} {now - since:.3f}s'
                         for name, (action, since) in pending.items())

    next_update = start + 3
    while True:
        now = monotonic()
        if now >= next_update:
            emit('pending: ' + outstanding() if pending else 'awaiting Compose progress')
            next_update = now + 3
        try:
            line = lines.get(timeout=next_update - now)
        except Empty:
            continue
        if line is None:
            break
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        if not (isinstance(event, dict) and isinstance(event.get('id'), str)
                and event['id'].startswith('Container ') and isinstance(event.get('text'), str)
                and event.get('status') in ('Working', 'Done')):
            # Preserve Compose diagnostics, including errors and non-container progress.
            print(line, end='', flush=True)
            continue

        name = event['id'][len('Container '):]
        action = event['text']
        now = monotonic()
        previous = pending.get(name)
        if event['status'] == 'Working':
            if previous and previous[0] == action:
                continue
            pending[name] = (action, now)
        else:
            pending.pop(name, None)
        duration = f' ({previous[0]} {now - previous[1]:.3f}s)' if previous else ''
        emit(f'{name} {action}{duration}')

    if pending:
        emit('last reported pending: ' + outstanding())
    emit('end')


if __name__ == '__main__':
    report(sys.argv[1])
