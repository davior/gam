"""Semantic search: storing vectors, and comparing them.

Brute force, in numpy, over every vector a user owns. That sounds naive and is the right
answer at this scale: 512 float32 values is 2 KB, so fifty thousand transcript segments
is about a hundred megabytes and a single matrix multiply — low milliseconds. A vector
database would add a service, a schema, a sync problem and an operational burden to beat
a number that is already imperceptible.

The upgrade path, when a library outgrows this, is sqlite-vec: same file, same process,
an index instead of a scan. Nothing here is structured in a way that would make that
hard, which is the point of not reaching for it yet.
"""

from __future__ import annotations

import logging
import struct
import threading
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
from sqlmodel import Session, col, delete, select

from app.models.embedding import Embedding, OWNER_ASSET, OWNER_SEGMENT

logger = logging.getLogger(__name__)


@dataclass
class VectorHit:
    owner_kind: str
    owner_id: str
    asset_id: str
    similarity: float


# ─── packing ─────────────────────────────────────────────────────────────────


def pack(vector: Sequence[float]) -> bytes:
    """float32 rather than float64: half the memory, and the precision difference is
    far below the noise floor of the embedding itself."""
    return np.asarray(vector, dtype=np.float32).tobytes()


def unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


# ─── writing ─────────────────────────────────────────────────────────────────


def replace_for_asset(
    session: Session,
    *,
    user_id: str,
    asset_id: str,
    owner_kind: str,
    rows: Sequence[tuple[str, Sequence[float]]],
    model: str,
) -> int:
    """Replace every vector of one kind for one asset.

    Replacement, because that is what re-embedding is: the text changed, so the old
    vectors describe something that no longer exists.
    """
    session.exec(
        delete(Embedding)
        .where(Embedding.asset_id == asset_id)
        .where(Embedding.owner_kind == owner_kind)
    )

    written = 0
    for owner_id, vector in rows:
        if not len(vector):
            continue
        session.add(
            Embedding(
                user_id=user_id,
                owner_kind=owner_kind,
                owner_id=owner_id,
                asset_id=asset_id,
                model=model,
                dim=len(vector),
                vector=pack(vector),
            )
        )
        written += 1

    session.commit()
    invalidate(user_id)
    return written


def remove_for_asset(session: Session, user_id: str, asset_id: str) -> None:
    session.exec(delete(Embedding).where(Embedding.asset_id == asset_id))
    session.commit()
    invalidate(user_id)


# ─── the in-process cache ────────────────────────────────────────────────────
#
# Loading and unpacking every vector on every keystroke would dominate the search. The
# matrix is rebuilt only when something writes.

_lock = threading.Lock()
_cache: dict[str, "_Matrix"] = {}


@dataclass
class _Matrix:
    model: str
    vectors: np.ndarray          # (n, dim), L2-normalised
    owners: list[tuple[str, str, str]]  # (owner_kind, owner_id, asset_id)


def invalidate(user_id: Optional[str] = None) -> None:
    with _lock:
        if user_id is None:
            _cache.clear()
        else:
            _cache.pop(user_id, None)


def _load(session: Session, user_id: str, model: str) -> Optional[_Matrix]:
    rows = session.exec(
        select(Embedding).where(Embedding.user_id == user_id, Embedding.model == model)
    ).all()
    if not rows:
        return None

    # A row whose width disagrees with the rest cannot be compared and is skipped rather
    # than crashing the search — that happens when the dimension setting changes and a
    # backfill has not caught up.
    width = max((row.dim for row in rows), default=0)
    usable = [row for row in rows if row.dim == width and len(row.vector) == width * 4]
    skipped = len(rows) - len(usable)
    if skipped:
        logger.info("Skipped %d embedding(s) of a different width for %s", skipped, user_id)
    if not usable:
        return None

    matrix = np.vstack([unpack(row.vector) for row in usable]).astype(np.float32)

    # Normalise once, at load. Cosine similarity is then a plain dot product, and the
    # per-query cost drops to one matrix multiply.
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix /= norms

    return _Matrix(
        model=model,
        vectors=matrix,
        owners=[(row.owner_kind, row.owner_id, row.asset_id) for row in usable],
    )


def search(
    session: Session,
    user_id: str,
    query_vector: Sequence[float],
    *,
    model: str,
    limit: int = 50,
    min_similarity: float = 0.0,
) -> list[VectorHit]:
    """The nearest vectors to a query, most similar first."""
    if not len(query_vector):
        return []

    with _lock:
        matrix = _cache.get(user_id)
        if matrix is None or matrix.model != model:
            matrix = _load(session, user_id, model)
            if matrix is None:
                return []
            _cache[user_id] = matrix

    query = np.asarray(query_vector, dtype=np.float32)
    if query.shape[0] != matrix.vectors.shape[1]:
        # The stored vectors were made by a different model or dimension setting. Saying
        # nothing beats returning nonsense ranked confidently.
        logger.info(
            "Query width %d does not match stored width %d; skipping semantic search",
            query.shape[0],
            matrix.vectors.shape[1],
        )
        return []

    norm = float(np.linalg.norm(query))
    if norm == 0:
        return []
    query = query / norm

    similarities = matrix.vectors @ query

    # argpartition rather than a full sort: only the top `limit` matter, and this is
    # O(n) against O(n log n) on a matrix that can be large.
    count = min(limit, similarities.shape[0])
    top = np.argpartition(-similarities, count - 1)[:count]
    top = top[np.argsort(-similarities[top])]

    hits = []
    for index in top:
        score = float(similarities[index])
        if score < min_similarity:
            break
        kind, owner_id, asset_id = matrix.owners[index]
        hits.append(
            VectorHit(owner_kind=kind, owner_id=owner_id, asset_id=asset_id, similarity=score)
        )
    return hits
