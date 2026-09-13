"""What an embedding provider has to do.

Deliberately small. An embedder turns text into vectors and says how wide they are;
everything else — batching policy, storage, similarity — belongs to the caller, so
adding a provider never means reimplementing search.
"""

from __future__ import annotations

from typing import Protocol, Sequence


class EmbeddingError(Exception):
    """The provider refused, failed, or returned something unusable."""


class Embedder(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per input, in the same order.

        Order is part of the contract: callers match vectors back to rows by position,
        and a provider that reorders would silently attach every embedding to the wrong
        segment.
        """
        ...
