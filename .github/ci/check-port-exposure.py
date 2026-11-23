"""Exercise the production verifier inside an isolated Linux network namespace."""
import socket
import subprocess
from contextlib import ExitStack

with ExitStack() as sockets:
    for port in (3000, 4000, 8000, 8001, 9090, 12434):
        sockets.enter_context(socket.create_server(("127.0.0.1", port)))
    verify = ["bash", "scripts/host-ports.sh", "verify"]
    subprocess.run(verify, check=True)

    sockets.enter_context(socket.create_server(("192.0.2.10", 8001)))
    result = subprocess.run(verify, capture_output=True, text=True)
    if result.returncode == 0:
        raise SystemExit("Port verifier accepted an extra listener on 192.0.2.10:8001")
    if "8001" not in result.stdout + result.stderr:
        raise SystemExit(f"Port verifier failed for an unexpected reason: {result.stderr}")
