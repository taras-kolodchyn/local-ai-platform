from pathlib import Path

from local_ai_retrieval.scanner import ScanStats, scan_repository


def test_scanner_respects_gitignore_and_sensitive_files(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('safe')\n", encoding="utf-8")
    (tmp_path / "ignored.py").write_text("print('ignored')\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=do-not-index\n", encoding="utf-8")
    (tmp_path / "private.pem").write_text("not-a-real-key\n", encoding="utf-8")
    (tmp_path / "leak.md").write_text(
        "-----BEGIN " + "PRIVATE KEY-----\nfixture\n", encoding="utf-8"
    )
    (tmp_path / "credentials.toml").write_text("placeholder = true\n", encoding="utf-8")
    stats = ScanStats()

    files = list(scan_repository(tmp_path, stats))

    assert [item.relative_path for item in files] == ["main.py"]
    assert stats.skipped_sensitive == 4


def test_scanner_allows_env_example(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("TOKEN=GENERATE_ME\n", encoding="utf-8")
    files = list(scan_repository(tmp_path))
    assert [item.relative_path for item in files] == [".env.example"]


def test_scanner_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside the repository", encoding="utf-8")
    (workspace / "linked.md").symlink_to(outside)

    files = list(scan_repository(workspace))

    assert files == []
