from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


SYMBOL_PATTERNS = (
    re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
    re.compile(r"^\s*func\s+([A-Za-z_][A-Za-z0-9_]*)"),
)


@dataclass(frozen=True)
class Chunk:
    index: int
    start_line: int
    end_line: int
    content: str
    symbol_name: str | None
    content_hash: str


def _symbol(lines: list[str]) -> str | None:
    for line in lines:
        for pattern in SYMBOL_PATTERNS:
            match = pattern.match(line)
            if match:
                return match.group(1)
    return None


def chunk_text(
    text: str,
    *,
    max_lines: int = 160,
    overlap_lines: int = 20,
    max_chars: int = 12000,
) -> list[Chunk]:
    if max_lines <= overlap_lines:
        raise ValueError("max_lines must be greater than overlap_lines")

    lines = text.splitlines()
    if not lines:
        return []

    chunks: list[Chunk] = []
    start = 0
    while start < len(lines):
        end = min(start + max_lines, len(lines))
        selected = lines[start:end]

        while len("\n".join(selected)) > max_chars and len(selected) > 1:
            selected = selected[: max(1, len(selected) // 2)]
            end = start + len(selected)

        content = "\n".join(selected).strip()
        if content:
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            chunks.append(
                Chunk(
                    index=len(chunks),
                    start_line=start + 1,
                    end_line=end,
                    content=content,
                    symbol_name=_symbol(selected),
                    content_hash=digest,
                )
            )

        if end >= len(lines):
            break
        start = max(start + 1, end - overlap_lines)

    return chunks
