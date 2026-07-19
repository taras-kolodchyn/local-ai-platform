from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

import psycopg
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from psycopg.rows import dict_row
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

ROOT = Path(os.getenv("MCP_ROOT", "/workspace")).resolve()
DATABASE_URL = os.environ["DATABASE_URL"]
ALLOWED_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp", ".html",
    ".java", ".js", ".json", ".jsx", ".kt", ".kts", ".md", ".php",
    ".proto", ".py", ".rb", ".rs", ".sh", ".sql", ".swift", ".toml",
    ".ts", ".tsx", ".txt", ".yaml", ".yml",
}
ALLOWED_NAMES = {"AGENTS.md", "Containerfile", "Dockerfile", "Makefile", ".env.example"}
DENIED_PARTS = {".git", ".local", ".secrets", "node_modules", "secrets", "target", "vendor"}
SENSITIVE_NAMES = {".env", ".npmrc", ".pypirc", "credentials.json", "secrets.json"}
SENSITIVE_SUFFIXES = {".key", ".p12", ".pfx", ".pem", ".jks", ".keystore"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)(?:api[_-]?key|client[_-]?secret|access[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{20,}"),
    re.compile(r"(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
)
READ_QUERY = re.compile(r"^\s*(?:select|with)\b", re.IGNORECASE)
FORBIDDEN_SQL = re.compile(
    r"\b(?:alter|copy|create|delete|drop|execute|grant|insert|listen|notify|reindex|reset|revoke|set|truncate|update|vacuum)\b",
    re.IGNORECASE,
)

LOGGER = logging.getLogger("local_ai_offline_mcp")
TOOL_CALLS = Counter("local_ai_mcp_tool_calls_total", "Offline MCP tool calls", ["tool", "status"])
TOOL_DURATION = Histogram("local_ai_mcp_tool_duration_seconds", "Offline MCP tool duration", ["tool"])
T = TypeVar("T")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": int(time.time()),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("event", "tool", "duration_ms", "result_count", "error_class"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def configure_logging() -> None:
    formatter = JsonFormatter()
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    handlers: list[logging.Handler] = [stream]
    if path := os.getenv("SERVICE_LOG_FILE"):
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), handlers=handlers, force=True)


def observed(tool: str, operation: Callable[[], T]) -> T:
    started = time.monotonic()
    try:
        result = operation()
    except Exception as exc:
        TOOL_CALLS.labels(tool=tool, status="error").inc()
        LOGGER.warning(
            "mcp_tool_failed",
            extra={"event": "mcp_tool_failed", "tool": tool, "error_class": type(exc).__name__},
        )
        raise
    duration = time.monotonic() - started
    TOOL_CALLS.labels(tool=tool, status="success").inc()
    TOOL_DURATION.labels(tool=tool).observe(duration)
    LOGGER.info(
        "mcp_tool_completed",
        extra={"event": "mcp_tool_completed", "tool": tool, "duration_ms": round(duration * 1000, 2)},
    )
    return result


def safe_path(relative_text: str) -> Path:
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("path must be relative and cannot contain '..'")
    if any(part in DENIED_PARTS for part in relative.parts):
        raise ValueError("path is outside the read-only allowlist")
    lower_name = relative.name.lower()
    if (
        lower_name in SENSITIVE_NAMES
        or (lower_name.startswith(".env.") and lower_name != ".env.example")
        or relative.stem.lower() in {"credential", "credentials", "secret", "secrets"}
        or relative.suffix.lower() in SENSITIVE_SUFFIXES
    ):
        raise ValueError("sensitive paths are not readable")
    path = (ROOT / relative).resolve()
    path.relative_to(ROOT)
    return path


def validate_select(sql: str) -> str:
    statement = sql.strip().rstrip(";")
    if ";" in statement or not READ_QUERY.match(statement) or FORBIDDEN_SQL.search(statement):
        raise ValueError("only one read-only SELECT or CTE statement is allowed")
    return statement


mcp = FastMCP(
    "local_tools",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "mcp-tools:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    ),
)
mcp.settings.streamable_http_path = "/"


@mcp.tool()
def list_files(pattern: str = "**/*", limit: int = 100) -> str:
    """List allowlisted source/document files under the configured read-only root."""
    def operation() -> str:
        if pattern.startswith("/") or ".." in Path(pattern).parts:
            raise ValueError("pattern must stay inside the configured root")
        bounded = min(max(limit, 1), 500)
        results: list[str] = []
        for path in ROOT.glob(pattern):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT)
            if any(part in DENIED_PARTS for part in relative.parts):
                continue
            if relative.name not in ALLOWED_NAMES and relative.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            try:
                safe_path(relative.as_posix())
            except ValueError:
                continue
            results.append(relative.as_posix())
            if len(results) >= bounded:
                break
        return json.dumps(sorted(results), separators=(",", ":"))
    return observed("list_files", operation)


@mcp.tool()
def read_text_file(path: str, start_line: int = 1, end_line: int = 200) -> str:
    """Read a bounded range from an allowlisted text file; secrets and arbitrary paths are denied."""
    def operation() -> str:
        target = safe_path(path)
        if target.name not in ALLOWED_NAMES and target.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ValueError("file type is not allowlisted")
        if not target.is_file() or target.stat().st_size > 1_000_000:
            raise ValueError("file is missing or too large")
        first = max(start_line, 1)
        last = min(max(end_line, first), first + 399)
        text = target.read_text(encoding="utf-8", errors="replace")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            raise ValueError("file content matches a high-confidence secret pattern")
        lines = text.splitlines()
        content = "\n".join(lines[first - 1:last])
        return json.dumps({"path": path, "start_line": first, "end_line": min(last, len(lines)), "content": content}, separators=(",", ":"), ensure_ascii=True)
    return observed("read_text_file", operation)


def git_command(arguments: list[str], timeout: int = 10) -> str:
    result = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(ROOT),
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={
            "PATH": os.environ.get("PATH", ""),
            "GIT_PAGER": "cat",
            "LC_ALL": "C",
        },
    )
    return result.stdout[:100_000]


@mcp.tool()
def git_status() -> str:
    """Return read-only Git branch and status metadata for the configured root."""
    return observed("git_status", lambda: git_command(["status", "--short", "--branch"]))


@mcp.tool()
def git_log(limit: int = 10) -> str:
    """Return a bounded read-only Git commit summary."""
    bounded = min(max(limit, 1), 50)
    return observed("git_log", lambda: git_command(["log", f"-{bounded}", "--date=iso-strict", "--pretty=format:%H%x09%ad%x09%s"]))


@mcp.tool()
def postgres_select(sql: str, parameters: list[str] | None = None, max_rows: int = 100) -> str:
    """Run one bounded read-only SELECT/CTE against the platform database."""
    def operation() -> str:
        statement = validate_select(sql)
        bounded = min(max(max_rows, 1), 500)
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SET LOCAL statement_timeout = '5s'")
                rows = connection.execute(statement, parameters or []).fetchmany(bounded)
        return json.dumps(rows, separators=(",", ":"), ensure_ascii=True, default=str)
    return observed("postgres_select", operation)


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "offline-mcp"})


async def metrics(_: Request) -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@contextlib.asynccontextmanager
async def lifespan(_: Starlette):
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/metrics", metrics, methods=["GET"]),
        Mount("/mcp", app=mcp.streamable_http_app()),
    ],
    lifespan=lifespan,
)


def main() -> None:
    configure_logging()
    uvicorn.run(app, host="0.0.0.0", port=8001, access_log=False)
