from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    litellm_base_url: str
    litellm_api_key_file: Path
    embedding_model_alias: str
    embedding_dimensions: int
    default_limit: int
    max_limit: int
    max_context_chars: int
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ["DATABASE_URL"],
            litellm_base_url=os.getenv("LITELLM_BASE_URL", "http://litellm:4000/v1").rstrip("/"),
            litellm_api_key_file=Path(
                os.getenv("LITELLM_API_KEY_FILE", "/run/secrets/litellm_client_key")
            ),
            embedding_model_alias=os.getenv("EMBEDDING_MODEL_ALIAS", "local-embeddings"),
            embedding_dimensions=_int_env("EMBEDDING_DIMENSIONS", 1024),
            default_limit=_int_env("RETRIEVAL_DEFAULT_LIMIT", 6),
            max_limit=_int_env("RETRIEVAL_MAX_LIMIT", 12),
            max_context_chars=_int_env("RETRIEVAL_MAX_CONTEXT_CHARS", 24000),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )

    def api_key(self) -> str:
        key = self.litellm_api_key_file.read_text(encoding="utf-8").strip()
        if not key:
            raise RuntimeError("LiteLLM client key file is empty")
        return key
