import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@127.0.0.1/unused")

from local_ai_offline_mcp import service


def test_safe_path_rejects_secrets_and_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "ROOT", tmp_path.resolve())
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("fn main() {}", encoding="utf-8")

    assert service.safe_path("src/lib.rs") == (tmp_path / "src" / "lib.rs")
    with pytest.raises(ValueError):
        service.safe_path("../outside.txt")
    with pytest.raises(ValueError):
        service.safe_path(".env")
    with pytest.raises(ValueError):
        service.safe_path("config/credentials.toml")


def test_safe_path_rejects_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("not reachable", encoding="utf-8")
    (workspace / "linked.txt").symlink_to(outside)
    monkeypatch.setattr(service, "ROOT", workspace.resolve())

    with pytest.raises(ValueError):
        service.safe_path("linked.txt")


def test_read_query_policy_is_conservative() -> None:
    assert service.validate_select("SELECT repository FROM source_chunks LIMIT 1;").startswith("SELECT")
    assert service.validate_select("WITH recent AS (SELECT 1) SELECT * FROM recent").startswith("WITH")
    with pytest.raises(ValueError):
        service.validate_select("DELETE FROM source_chunks")
    with pytest.raises(ValueError):
        service.validate_select("SELECT 1; DROP TABLE source_chunks")
    with pytest.raises(ValueError):
        service.validate_select("WITH changed AS (UPDATE source_chunks SET content = '') SELECT 1")


def test_git_commands_disable_repository_fsmonitor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)

    marker = tmp_path / "fsmonitor-ran"
    monitor = tmp_path / "fsmonitor.sh"
    monitor.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    monitor.chmod(0o700)
    subprocess.run(
        ["git", "-C", str(repository), "config", "core.fsmonitor", str(monitor)],
        check=True,
    )
    monkeypatch.setattr(service, "ROOT", repository.resolve())

    service.git_command(["status", "--short"])

    assert not marker.exists()
