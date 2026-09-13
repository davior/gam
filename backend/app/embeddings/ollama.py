"""Ollama embeddings — the option that needs no key and sends nothing anywhere.

For a library of private material that matters: every other provider means shipping
every transcript line to a third party. A local Ollama keeps them on the machine.

Uses `/api/embed`, not the older `/api/embeddings`. The newer endpoint batches (the old
one takes a single `prompt` and returns a single `embedding`), which is the difference
between one request and ten thousand on a backfill. It also returns L2-normalised
vectors already, so normalising them again is a no-op rather than a correction.
"""

from __future__ import annotations

import logging
from typing import Sequence

import httpx

from app.embeddings.base import EmbeddingError

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "nomic-embed-text"
DEFAULT_BASE_URL = "http://localhost:11434"

# Local inference on CPU is not fast, and a backfill batch can be large.
REQUEST_TIMEOUT = httpx.Timeout(600.0, connect=10.0)

MAX_BATCH = 64


class OllamaEmbedder:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, *, model: str = DEFAULT_MODEL, dimensions: int = 768):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimensions = dimensions

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_BATCH):
            vectors.extend(self._embed_batch(list(texts[start : start + MAX_BATCH])))
        return vectors

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        try:
            response = httpx.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": batch},
                timeout=REQUEST_TIMEOUT,
            )
        except httpx.TimeoutException as exc:
            raise EmbeddingError("Ollama did not respond in time") from exc
        except httpx.RequestError as exc:
            raise EmbeddingError(
                f"Could not reach Ollama at {self._base_url}: {type(exc).__name__}"
            ) from exc

        if response.status_code == 404:
            # The most common misconfiguration by far, and the message says the fix.
            raise EmbeddingError(
                f"Ollama has no model '{self._model}'. Run: ollama pull {self._model}"
            )
        if response.status_code >= 400:
            raise EmbeddingError(f"Ollama returned {response.status_code}")

        try:
            body = response.json()
        except ValueError as exc:
            raise EmbeddingError("Ollama returned a response that was not JSON") from exc

        vectors = body.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(batch):
            raise EmbeddingError("Ollama returned a different number of vectors than inputs")

        return [list(v) for v in vectors]
