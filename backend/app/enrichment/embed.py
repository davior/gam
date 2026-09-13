"""The embed job: turn an asset's text into vectors so it can be found by meaning.

Two kinds of text, embedded separately because they answer different questions. Each
transcript segment becomes its own vector, which is what lets a hit carry a timestamp.
The asset's own name, description and summary become a single vector, so a photograph
with no speech in it is still findable by what it is of.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from sqlmodel import Session, col, select

from app.embeddings import EmbeddingError, build_embedder
from app.models.asset import Asset
from app.models.embedding import OWNER_ASSET, OWNER_SEGMENT
from app.models.transcript import TranscriptSegment
from app.search import vectors

logger = logging.getLogger(__name__)

Progress = Callable[..., None]


class EmbeddingUnavailable(Exception):
    """No embedder is configured for this user."""


def run(session: Session, asset: Asset, progress: Progress) -> int:
    """Embed one asset. Returns how many vectors were written."""
    embedder = build_embedder(session, asset.user_id)
    if embedder is None:
        raise EmbeddingUnavailable(
            "No embedding provider is configured. Add one in Settings to enable "
            "semantic search."
        )

    written = 0

    progress("Embedding metadata", 10, "")
    written += _embed_asset_text(session, asset, embedder)

    segments = session.exec(
        select(TranscriptSegment)
        .where(TranscriptSegment.asset_id == asset.id)
        .order_by(col(TranscriptSegment.idx))
    ).all()
    segments = [s for s in segments if (s.text or "").strip()]

    if segments:
        progress("Embedding transcript", 40, f"{len(segments)} segments")
        written += _embed_segments(session, asset, segments, embedder)

    return written


def _embed_asset_text(session: Session, asset: Asset, embedder) -> int:
    """One vector for what the asset *is*, as opposed to what is said in it."""
    parts = [asset.name or "", asset.description or "", asset.summary or ""]
    text = "\n".join(part for part in parts if part.strip()).strip()
    if not text:
        return 0

    try:
        vector = embedder.embed([text])[0]
    except EmbeddingError:
        raise
    except Exception as exc:  # noqa: BLE001 - a provider may raise its own types
        raise EmbeddingError(f"Embedding failed: {type(exc).__name__}") from exc

    return vectors.replace_for_asset(
        session,
        user_id=asset.user_id,
        asset_id=asset.id,
        owner_kind=OWNER_ASSET,
        rows=[(asset.id, vector)],
        model=embedder.model,
    )


def _embed_segments(session: Session, asset: Asset, segments, embedder) -> int:
    try:
        computed = embedder.embed([s.text for s in segments])
    except EmbeddingError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise EmbeddingError(f"Embedding failed: {type(exc).__name__}") from exc

    if len(computed) != len(segments):
        # The protocol promises one vector per input in order; a provider that breaks
        # that would attach every embedding to the wrong segment, which is worse than
        # having none.
        raise EmbeddingError("The provider returned a different number of vectors than segments")

    return vectors.replace_for_asset(
        session,
        user_id=asset.user_id,
        asset_id=asset.id,
        owner_kind=OWNER_SEGMENT,
        rows=list(zip((s.id for s in segments), computed)),
        model=embedder.model,
    )


def embeddable(asset: Asset) -> bool:
    """Whether there is anything worth embedding.

    True for essentially everything: a name alone is enough, and a name is required.
    Kept as a function so the caller does not have to know that.
    """
    return bool((asset.name or "").strip())
