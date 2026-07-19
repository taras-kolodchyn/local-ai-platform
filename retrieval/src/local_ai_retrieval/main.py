from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from .config import Settings
from .ingestion import index_repository
from .logging_config import configure_logging


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="local-ai-retrieval")
    commands = root.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="Run the REST, metrics, and MCP server")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)

    for name in ("index", "reindex"):
        ingest = commands.add_parser(name, help=f"{name} a repository")
        ingest.add_argument("path", type=Path)
        ingest.add_argument("--repository")
    return root


def main() -> None:
    args = parser().parse_args()
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    if args.command == "serve":
        uvicorn.run(
            "local_ai_retrieval.service:app",
            host=args.host,
            port=args.port,
            log_level=settings.log_level.lower(),
            access_log=False,
        )
        return

    result = index_repository(
        args.path,
        settings,
        repository=args.repository,
        force=args.command == "reindex",
    )
    # Contains counts and Git metadata only, never source bodies or credentials.
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
