"""Read-only aggregate task telemetry without user or source labels."""
from collections import Counter, defaultdict
from contextlib import closing
import hmac
import os
from pathlib import Path
import sqlite3

PHASES = ('preparing', 'analyzing', 'implementing', 'editing', 'verifying',
          'verified', 'delivering', 'completed', 'failed', 'timed_out', 'cancelled', 'uncertain')
SETTLED = {'completed', 'failed', 'timed_out', 'cancelled'}
BUCKETS = (1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600)


def histogram(name, observations, labels=''):
    suffix = ',' + labels if labels else ''
    lines = [f'{name}_bucket{{le="{bound}"{suffix}}} {sum(value <= bound for value in observations)}'
             for bound in BUCKETS]
    lines.append(f'{name}_bucket{{le="+Inf"{suffix}}} {len(observations)}')
    tag = '{' + labels + '}' if labels else ''
    lines.extend([f'{name}_sum{tag} {sum(observations):g}',
                  f'{name}_count{tag} {len(observations)}'])
    return lines


def render_metrics(path=Path('/data/local-tasks.db')):
    try:
        path = Path(path)
        if not path.is_file():
            return 'local_repository_task_metrics_up 0\n'
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            tasks = db.execute('SELECT session_id,phase,created FROM tasks').fetchall()
            events = db.execute('SELECT session_id,phase,ts FROM events ORDER BY session_id,ts,rowid').fetchall()
        groups = defaultdict(list)
        for session, phase, timestamp in events:
            if phase in PHASES:
                groups[session].append((phase, timestamp))
        phases, durations = defaultdict(list), []
        for session, phase, created in tasks:
            history = groups[session]
            for (previous, started), (_, ended) in zip(history, history[1:]):
                if previous not in SETTLED and ended >= started:
                    phases[previous].append(ended - started)
            if phase in SETTLED and history and history[-1][0] == phase:
                finished = history[-1][1]
                if finished >= created:
                    durations.append(finished - created)
        counts = Counter(phase for _, phase, _ in tasks if phase in PHASES)
        result = ['local_repository_task_metrics_up 1', '# TYPE local_repository_tasks gauge']
        result.extend(f'local_repository_tasks{{phase="{phase}"}} {counts[phase]}' for phase in PHASES)
        result.append('# TYPE local_repository_task_duration_seconds histogram')
        result.extend(histogram('local_repository_task_duration_seconds', durations))
        result.append('# TYPE local_repository_phase_duration_seconds histogram')
        for phase in PHASES:
            result.extend(histogram('local_repository_phase_duration_seconds', phases[phase], f'phase="{phase}"'))
        return '\n'.join(result) + '\n'
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return 'local_repository_task_metrics_up 0\n'


def install_metrics(app):
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import Response
    from starlette.routing import Route

    async def endpoint(request):
        expected = os.environ.get('MOYAI_REPOSITORY_CONTEXT_TOKEN', '')
        supplied = request.headers.get('authorization', '')
        if not expected or not hmac.compare_digest(supplied.encode(), ('Bearer ' + expected).encode()):
            return Response(status_code=401)
        return Response(await run_in_threadpool(render_metrics), media_type='text/plain; version=0.0.4')

    # Native Moyai also installs a SPA catch-all Route, not only a StaticFiles Mount.
    app.router.routes.insert(0, Route('/local/repository-metrics', endpoint, methods=['GET']))
