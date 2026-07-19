from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from .chunker import chunk_text
from .config import Settings
from .db import (
    ChunkRecord,
    apply_snapshot,
    create_ingestion_run,
    delete_snapshot,
    existing_hashes,
    finish_ingestion_run,
    migrate,
)
from .embeddings import embed_texts
from .metrics import INGESTION_CHUNKS, INGESTION_DURATION, INGESTION_FILES
from .scanner import ScanStats, scan_repository

LOGGER = logging.getLogger(__name__)
BATCH_SIZE = 16


def _git_value(root: Path, args: list[str], fallback: str) -> str:
    try:
        result = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-C",
                str(root),
                *args,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        value = result.stdout.strip()
        return value or fallback
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return fallback


def repository_identity(root: Path, repository: str | None = None) -> tuple[str, str, str]:
    name = repository or root.resolve().name
    branch = _git_value(root, ["branch", "--show-current"], "detached")
    commit = _git_value(root, ["rev-parse", "HEAD"], "unversioned")
    return name, branch, commit


def index_repository(
    root: Path,
    settings: Settings,
    *,
    repository: str | None = None,
    force: bool = False,
) -> dict[str, int | str]:
    root = root.resolve()
    started = time.monotonic()
    migrate(settings)
    repository_name, branch, commit_hash = repository_identity(root, repository)
    if force:
        delete_snapshot(settings, repository_name, branch)

    hashes = existing_hashes(settings, repository_name, branch)
    run_id = create_ingestion_run(settings, repository_name, branch, commit_hash)
    scan_stats = ScanStats()
    live_keys: list[tuple[str, int]] = []
    pending: list[tuple[object, object]] = []
    records: list[ChunkRecord] = []
    reused = 0

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        vectors = embed_texts([chunk.content for _, chunk in pending], settings)
        for (source, chunk), vector in zip(pending, vectors, strict=True):
            records.append(
                ChunkRecord(
                    repository=repository_name,
                    branch=branch,
                    commit_hash=commit_hash,
                    path=source.relative_path,
                    language=source.language,
                    symbol_name=chunk.symbol_name,
                    chunk_index=chunk.index,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    chunk_hash=chunk.content_hash,
                    content=chunk.content,
                    metadata={
                        "repository": repository_name,
                        "branch": branch,
                        "commit_hash": commit_hash,
                        "path": source.relative_path,
                        "language": source.language,
                        "symbol_name": chunk.symbol_name,
                    },
                    embedding=vector,
                )
            )
            INGESTION_CHUNKS.labels(status="embedded").inc()
        pending = []

    try:
        for source in scan_repository(root, scan_stats):
            INGESTION_FILES.labels(status="accepted").inc()
            for chunk in chunk_text(source.text):
                key = (source.relative_path, chunk.index)
                live_keys.append(key)
                if hashes.get(key) == chunk.content_hash:
                    reused += 1
                    INGESTION_CHUNKS.labels(status="reused").inc()
                    continue
                pending.append((source, chunk))
                if len(pending) >= BATCH_SIZE:
                    flush()
        flush()
        deleted = apply_snapshot(
            settings,
            records,
            live_keys,
            repository=repository_name,
            branch=branch,
            commit_hash=commit_hash,
        )
        duration = time.monotonic() - started
        finish_ingestion_run(
            settings,
            run_id,
            status="success",
            files_seen=scan_stats.seen,
            files_indexed=scan_stats.accepted,
            chunks_embedded=len(records),
            chunks_reused=reused,
            chunks_deleted=deleted,
            skipped_sensitive=scan_stats.skipped_sensitive,
            duration_ms=round(duration * 1000, 2),
        )
    except Exception as exc:
        duration = time.monotonic() - started
        INGESTION_DURATION.labels(status="error").observe(duration)
        finish_ingestion_run(
            settings,
            run_id,
            status="error",
            files_seen=scan_stats.seen,
            files_indexed=scan_stats.accepted,
            chunks_embedded=len(records),
            chunks_reused=reused,
            skipped_sensitive=scan_stats.skipped_sensitive,
            duration_ms=round(duration * 1000, 2),
            error_class=type(exc).__name__,
        )
        LOGGER.exception(
            "ingestion_failed",
            extra={"event": "ingestion_failed", "error_class": type(exc).__name__},
        )
        raise

    INGESTION_DURATION.labels(status="success").observe(duration)
    LOGGER.info(
        "ingestion_completed",
        extra={
            "event": "ingestion_completed",
            "duration_ms": round(duration * 1000, 2),
            "result_count": len(live_keys),
        },
    )

    return {
        "repository": repository_name,
        "branch": branch,
        "commit_hash": commit_hash,
        "files_seen": scan_stats.seen,
        "files_indexed": scan_stats.accepted,
        "chunks_embedded": len(records),
        "chunks_reused": reused,
        "chunks_deleted": deleted,
        "skipped_sensitive": scan_stats.skipped_sensitive,
    }
