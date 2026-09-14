"""How much of a library semantic search can actually see.

Separate from `vectors.py` on purpose. That module deals in embeddings and asset id
strings and imports only `Embedding`; answering "what is missing" needs `Asset` and
`TranscriptSegment` too, and widening those imports is how a focused module becomes the
module that knows about everything.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, col, func, select

from app.models.asset import Asset
from app.models.embedding import Embedding
from app.models.transcript import TranscriptSegment


@dataclass
class Coverage:
    """What a user's library looks like to the semantic half of search."""

    model: str
    total_assets: int
    embedded_assets: int
    pending_assets: int
    # The real unit of work: one vector per segment, and a single long interview can
    # hold thousands. Assets alone would tell someone a 3-asset backfill is quick when
    # it is twenty minutes of provider calls.
    pending_segments: int


def _unembedded(user_id: str, model: str):
    """Predicate: this asset has no vector at all at this model.

    "At this model" is the whole point, not a detail. `vectors.search` refuses a cached
    matrix whose model differs from the configured one, so a vector left behind by a
    previous provider is not a partial answer — it is no answer, and the asset holding
    it is exactly as invisible as one that was never embedded.
    """
    return ~(
        select(Embedding.id)
        .where(
            Embedding.asset_id == Asset.id,
            Embedding.user_id == user_id,
            Embedding.model == model,
        )
        .exists()
    )


def pending_asset_ids(session: Session, user_id: str, model: str) -> list[str]:
    """Which assets need embedding, newest first.

    Newest first so someone watching a long run sees the material they most recently
    cared about become searchable first, rather than working forward from whatever they
    uploaded a year ago.

    Known imprecision, and the condition that would make it a lie: an asset holding only
    an asset-level vector and no segment vectors would be counted as done. `embed.run`
    always writes both in one pass and transcription re-embeds through
    `_chain_embedding`, so nothing reachable today produces that state. Any future path
    that writes only the asset-level vector — an M6 "re-embed after autotag", say — makes
    this query silently wrong.
    """
    return list(
        session.exec(
            select(Asset.id)
            .where(Asset.user_id == user_id, _unembedded(user_id, model))
            .order_by(col(Asset.upload_date).desc())
        ).all()
    )


def coverage(session: Session, user_id: str, model: str) -> Coverage:
    total = session.exec(
        select(func.count()).select_from(Asset).where(Asset.user_id == user_id)
    ).one()

    pending = session.exec(
        select(func.count())
        .select_from(Asset)
        .where(Asset.user_id == user_id, _unembedded(user_id, model))
    ).one()

    # A join rather than an `IN` over pending_asset_ids: that list can be thousands of
    # ids long, and SQLite has a bound on how many parameters a statement may carry.
    pending_segments = session.exec(
        select(func.count())
        .select_from(TranscriptSegment)
        .join(Asset, col(Asset.id) == col(TranscriptSegment.asset_id))
        .where(Asset.user_id == user_id, _unembedded(user_id, model))
    ).one()

    return Coverage(
        model=model,
        total_assets=total,
        embedded_assets=total - pending,
        pending_assets=pending,
        pending_segments=pending_segments,
    )
