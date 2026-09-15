"""OpenAI embeddings.

`text-embedding-3-small` at 512 dimensions rather than its native 1536. The model is
trained with Matryoshka representation, so a truncated vector keeps almost all of its
retrieval quality while costing a third of the memory — and memory is the constraint
here, because search holds every vector for a user in one numpy matrix.
"""

from __future__ import annotations

import logging
from typing import Sequence

import httpx

from app.embeddings.base import EmbeddingError

logger = logging.getLogger(__name__)

# A base rather than a full URL, so any OpenAI-compatible endpoint can serve this side
# too — a gateway, a self-hosted inference server, or DeepSeek if it publishes one. The
# generation providers have had a configurable base URL since they were ported from
# gecko-notes; this is the embedding half catching up (docs/m6-ai-enrichment.md).
DEFAULT_BASE_URL = "https://api.openai.com"
EMBEDDINGS_PATH = "/v1/embeddings"

DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_DIMENSIONS = 512

# Embedding is fast per item but a backfill sends thousands. Generous enough that a
# large batch is not cut off, short enough that a wedged request does not hold a worker.
REQUEST_TIMEOUT = httpx.Timeout(180.0, connect=15.0)

# Each request carries a whole batch. Well under the API's input cap, and small enough
# that one failure costs little work.
MAX_BATCH = 128


class OpenAIEmbedder:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        dimensions: int = DEFAULT_DIMENSIONS,
        base_url: str | None = None,
    ):
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._url = (base_url or DEFAULT_BASE_URL).rstrip("/") + EMBEDDINGS_PATH

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
        payload = {
            "model": self._model,
            "input": batch,
            "dimensions": self._dimensions,
        }
        try:
            response = httpx.post(
                self._url,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=REQUEST_TIMEOUT,
            )
        except httpx.TimeoutException as exc:
            raise EmbeddingError("OpenAI did not respond in time") from exc
        except httpx.RequestError as exc:
            raise EmbeddingError(f"Could not reach OpenAI: {type(exc).__name__}") from exc

        if response.status_code == 401:
            raise EmbeddingError("OpenAI rejected the API key")
        if response.status_code == 429:
            raise EmbeddingError("OpenAI rate limit or quota exceeded")
        if response.status_code >= 400:
            raise EmbeddingError(f"OpenAI returned {response.status_code}: {_detail(response)}")

        try:
            body = response.json()
        except ValueError as exc:
            raise EmbeddingError("OpenAI returned a response that was not JSON") from exc

        data = body.get("data")
        if not isinstance(data, list) or len(data) != len(batch):
            raise EmbeddingError("OpenAI returned a different number of vectors than inputs")

        # The API documents `index` and may return items out of order; sorting by it is
        # what makes the ordering guarantee in the protocol actually true.
        ordered = sorted(data, key=lambda item: item.get("index", 0))
        return [list(item.get("embedding") or []) for item in ordered]


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return (response.text or "").strip()[:200] or "no detail"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:200]
    return str(body)[:200]
