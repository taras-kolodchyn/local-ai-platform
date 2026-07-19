from __future__ import annotations

import math

import httpx

from .config import Settings
from .metrics import EMBEDDING_ITEMS, EMBEDDING_REQUESTS


class EmbeddingError(RuntimeError):
    pass


def embed_texts(texts: list[str], settings: Settings) -> list[list[float]]:
    if not texts:
        return []

    try:
        with httpx.Client(timeout=httpx.Timeout(300.0)) as client:
            response = client.post(
                f"{settings.litellm_base_url}/embeddings",
                headers={"Authorization": f"Bearer {settings.api_key()}"},
                json={
                    "model": settings.embedding_model_alias,
                    "input": texts,
                    "encoding_format": "float",
                    "cache": {"no-cache": True, "no-store": True},
                },
            )
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError, OSError) as exc:
        EMBEDDING_REQUESTS.labels(status="error").inc()
        raise EmbeddingError(f"Embedding request failed: {type(exc).__name__}") from exc

    ordered = sorted(body.get("data", []), key=lambda item: item.get("index", 0))
    vectors = [item.get("embedding") for item in ordered]
    if len(vectors) != len(texts):
        EMBEDDING_REQUESTS.labels(status="invalid_count").inc()
        raise EmbeddingError("Embedding response item count does not match input count")

    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != settings.embedding_dimensions:
            EMBEDDING_REQUESTS.labels(status="invalid_dimensions").inc()
            raise EmbeddingError(
                f"Expected {settings.embedding_dimensions} embedding dimensions"
            )
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector):
            EMBEDDING_REQUESTS.labels(status="invalid_value").inc()
            raise EmbeddingError("Embedding contains a non-finite value")

    EMBEDDING_REQUESTS.labels(status="success").inc()
    EMBEDDING_ITEMS.inc(len(texts))
    return vectors
