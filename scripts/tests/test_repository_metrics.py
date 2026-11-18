"""Operational metrics expose bounded aggregates rather than task identities."""
from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


class RepositoryMetricsTests(unittest.TestCase):
    def test_metrics_route_precedes_native_spa_catchall(self):
        module = __import__('runpy').run_path(str(ROOT/'integrations/moyai/repository_metrics.py'))
        app = Starlette(routes=[Route('/{path:path}',lambda _:Response('<html>SPA</html>',media_type='text/html'))])
        module['install_metrics'](app)
        with patch.dict('os.environ',{'MOYAI_REPOSITORY_CONTEXT_TOKEN':'fixture-token'}),TestClient(app) as client:
            self.assertEqual(client.get('/local/repository-metrics').status_code,401)
            response = client.get('/local/repository-metrics',headers={'Authorization':'Bearer fixture-token'})
            self.assertEqual(response.status_code,200)
            self.assertIn('local_repository_task_metrics_up',response.text)

    def test_metrics_route_precedes_the_native_root_static_mount(self):
        module = __import__('runpy').run_path(str(ROOT / 'integrations/moyai/repository_metrics.py'))
        with tempfile.TemporaryDirectory() as directory:
            app = Starlette(routes=[Mount('/', app=StaticFiles(directory=directory))])
            module['install_metrics'](app)
            with patch.dict('os.environ', {'MOYAI_REPOSITORY_CONTEXT_TOKEN': 'fixture-token'}), TestClient(app) as client:
                self.assertEqual(client.get('/local/repository-metrics').status_code, 401)
                result = client.get('/local/repository-metrics', headers={'Authorization': 'Bearer fixture-token'})
                self.assertEqual(result.status_code, 200)
                self.assertIn('local_repository_task_metrics_up', result.text)

    def test_metrics_aggregate_settled_task_and_phase_times_without_private_labels(self):
        spec = importlib.util.spec_from_file_location('repository_metrics',
            ROOT / 'integrations/moyai/repository_metrics.py')
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except FileNotFoundError:
            self.fail('Repository task metrics are missing')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tasks.db'
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript('CREATE TABLE tasks (session_id TEXT,phase TEXT,created REAL);'
                                 'CREATE TABLE events (session_id TEXT,phase TEXT,ts REAL);')
                db.execute('INSERT INTO tasks VALUES (?,?,?)', ('private-session', 'completed', 100))
                db.executemany('INSERT INTO events VALUES (?,?,?)', [
                    ('private-session', 'analyzing', 100),
                    ('private-session', 'failed', 102),
                    ('private-session', 'analyzing', 104),
                    ('private-session', 'implementing', 105),
                    ('private-session', 'verifying', 115),
                    ('private-session', 'completed', 130)])
            result = module.render_metrics(path)
            self.assertIn('local_repository_task_duration_seconds_sum 30', result)
            self.assertIn('local_repository_task_duration_seconds_count 1', result)
            self.assertIn('local_repository_phase_duration_seconds_sum{phase="verifying"} 15', result)
            self.assertIn('local_repository_tasks{phase="completed"} 1', result)
            self.assertNotIn('private-session', result)
            self.assertNotIn(str(path), result)
            self.assertIn('local_repository_task_metrics_up 1', result)
            absent = module.render_metrics(Path(directory) / 'missing.db')
            self.assertIn('local_repository_task_metrics_up 0', absent)
            self.assertFalse((Path(directory) / 'missing.db').exists())
