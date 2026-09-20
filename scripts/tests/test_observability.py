import json
import os
from pathlib import Path
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[2]


class MonitoringCheckTests(unittest.TestCase):
    def run_check(self, targets, timeout=2, metric=True):
        snapshots = iter(targets)
        last = targets[-1]

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                nonlocal last
                if self.path == "/api/v1/targets":
                    last = next(snapshots, last)
                    body = json.dumps({"status": "success", "data": {"activeTargets": last}})
                elif self.path == "/api/health":
                    body = '{"database":"ok"}'
                elif self.path == "/metrics":
                    body = "local_ai_retrieval_requests_total 1\n" if metric else "other_metric 1\n"
                else:
                    body = "ready"
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                return subprocess.run(
                    ["bash", str(ROOT / "scripts/check-observability.sh")],
                    env={**os.environ, "RETRIEVAL_BASE_URL": url, "PROMETHEUS_BASE_URL": url,
                         "GRAFANA_BASE_URL": url, "MONITORING_TIMEOUT_SECONDS": str(timeout)},
                    capture_output=True, text=True, timeout=10,
                )
            finally:
                server.shutdown()
                thread.join()

    def test_waits_for_scrape_after_restart(self):
        down = {"labels": {"job": "retrieval"}, "health": "down", "lastError": "connection refused"}
        up = {**down, "health": "up", "lastError": ""}
        result = self.run_check([[down], [up]], timeout=4)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("healthy", result.stdout)

    def test_reports_persistently_unhealthy_target(self):
        result = self.run_check([[{"labels": {"job": "retrieval"}, "health": "down",
                                  "lastError": "connection refused"}]], timeout=3)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("retrieval", result.stderr)
        self.assertIn("connection refused", result.stderr)

    def test_empty_target_list_is_not_success(self):
        result = self.run_check([[]], timeout=3)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No active", result.stderr)

    def test_missing_retrieval_metric_is_explained(self):
        result = self.run_check([[{"health": "up"}]], timeout=3, metric=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("retrieval metric", result.stderr)
