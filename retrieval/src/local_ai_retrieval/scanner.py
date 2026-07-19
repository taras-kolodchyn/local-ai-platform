from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pathspec


ALLOWED_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp", ".html",
    ".java", ".js", ".json", ".jsx", ".kt", ".kts", ".md", ".php",
    ".proto", ".py", ".rb", ".rs", ".scala", ".sh", ".sql", ".swift",
    ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml",
}
ALLOWED_NAMES = {"Dockerfile", "Makefile", "Containerfile", "AGENTS.md"}
DENIED_DIRS = {
    ".git", ".idea", ".venv", ".vscode", "build", "coverage", "dist",
    "node_modules", "target", "vendor", "__pycache__",
}
SENSITIVE_NAMES = {
    ".env", ".npmrc", ".pypirc", "credentials", "credentials.json",
    "id_dsa", "id_ecdsa", "id_ed25519", "id_rsa", "secrets.json",
}
SENSITIVE_SUFFIXES = {".key", ".p12", ".pfx", ".pem", ".jks", ".keystore"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)(?:api[_-]?key|client[_-]?secret|access[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{20,}"),
    re.compile(r"(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
)
MAX_FILE_BYTES = 1_000_000


LANGUAGES = {
    ".go": "go", ".java": "java", ".js": "javascript", ".jsx": "javascript",
    ".kt": "kotlin", ".kts": "kotlin", ".md": "markdown", ".py": "python",
    ".rb": "ruby", ".rs": "rust", ".sh": "shell", ".sql": "sql",
    ".swift": "swift", ".toml": "toml", ".ts": "typescript",
    ".tsx": "typescript", ".yaml": "yaml", ".yml": "yaml",
}


@dataclass(frozen=True)
class SourceFile:
    relative_path: str
    language: str
    text: str


@dataclass
class ScanStats:
    seen: int = 0
    accepted: int = 0
    skipped_sensitive: int = 0
    skipped_generated: int = 0
    skipped_binary_or_large: int = 0


def _git_paths(root: Path) -> list[str] | None:
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
                "ls-files",
                "-co",
                "--exclude-standard",
                "-z",
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [item.decode("utf-8", errors="surrogateescape") for item in result.stdout.split(b"\0") if item]


def _walk_paths(root: Path) -> list[str]:
    patterns: list[str] = []
    ignore_file = root / ".gitignore"
    if ignore_file.is_file():
        patterns = ignore_file.read_text(encoding="utf-8", errors="replace").splitlines()
    ignore = pathspec.GitIgnoreSpec.from_lines(patterns)
    return [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not ignore.match_file(path.relative_to(root).as_posix())
    ]


def _is_sensitive_path(relative: Path) -> bool:
    lower_name = relative.name.lower()
    if lower_name == ".env.example":
        return False
    return (
        lower_name in SENSITIVE_NAMES
        or (lower_name.startswith(".env.") and lower_name != ".env.example")
        or relative.stem.lower() in {"credential", "credentials", "secret", "secrets"}
        or relative.suffix.lower() in SENSITIVE_SUFFIXES
        or any(part.lower() in {"secrets", ".secrets"} for part in relative.parts)
    )


def scan_repository(root: Path, stats: ScanStats | None = None) -> Iterator[SourceFile]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Repository path is not a directory: {root}")
    stats = stats or ScanStats()
    paths = _git_paths(root) or _walk_paths(root)

    for relative_text in sorted(set(paths)):
        relative = Path(relative_text)
        stats.seen += 1
        if any(part in DENIED_DIRS for part in relative.parts):
            stats.skipped_generated += 1
            continue
        if _is_sensitive_path(relative):
            stats.skipped_sensitive += 1
            continue
        if (
            relative.name not in ALLOWED_NAMES
            and relative.name.lower() != ".env.example"
            and relative.suffix.lower() not in ALLOWED_EXTENSIONS
        ):
            continue

        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            stats.skipped_sensitive += 1
            continue
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            stats.skipped_binary_or_large += 1
            continue

        raw = path.read_bytes()
        if b"\0" in raw:
            stats.skipped_binary_or_large += 1
            continue
        text = raw.decode("utf-8", errors="replace")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            stats.skipped_sensitive += 1
            continue

        stats.accepted += 1
        yield SourceFile(
            relative_path=relative.as_posix(),
            language=LANGUAGES.get(relative.suffix.lower(), "text"),
            text=text,
        )
