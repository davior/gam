"""Embedding a whole library, for the content that was already there.

Transcription embeds what it produces, so anything ingested after a provider is
configured takes care of itself. This is for everything ingested before — which, on an
instance that has been running a while, is the entire library, and is invisible to
semantic search until something walks it.

One job walks the lot rather than queueing one job per asset. The deciding reason is
cancellation: `DELETE /api/activity/{kind}/{id}` stops one row, so per-asset jobs would
leave someone who thought better of a 500-asset run clicking Cancel five hundred times.
One row also means one progress bar and one line in the activity feed, instead of a wall
of near-identical rows that buries a concurrent transcription.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from sqlmodel import Session

from app import embeddings
from app.enrichment import embed
from app.enrichment.embed import EmbeddingUnavailable
from app.models.asset import Asset
from app.search import coverage

logger = logging.getLogger(__name__)

Progress = Callable[..., None]

# A run of failures indicts the provider, not the content. One asset with a pathological
# transcript must not kill a 500-asset run, but a rejected API key fails identically on
# every asset, and burning five hundred requests to discover that is slow, expensive and
# rude to the provider.
CONSECUTIVE_FAILURE_LIMIT = 3


@dataclass
class BackfillResult:
    embedded: int
    vectors: int
    failed: int


def run(session: Session, user_id: str, progress: Progress) -> BackfillResult:
    """Embed everything of this user's that has no vector at the configured model."""
    embedder = embeddings.build_embedder(session, user_id)
    if embedder is None:
        raise EmbeddingUnavailable(
            "No embedding provider is configured. Add one in Settings to enable "
            "semantic search."
        )

    # Resolved once, at the top. If the user changes model halfway through, this job
    # finishes filling the model it started on — the same rule `EnrichmentJob.model`
    # already states for transcription, and the alternative is a run that half-fills two
    # models and satisfies neither.
    model = embedder.model
    pending = coverage.pending_asset_ids(session, user_id, model)
    total = len(pending)

    embedded = vectors = failed = 0
    consecutive = 0

    for index, asset_id in enumerate(pending):
        asset = session.get(Asset, asset_id)
        if asset is None or asset.user_id != user_id:
            # Deleted between counting and reaching it. Not a failure.
            continue

        stage = "Embedding the library"
        pct = int(index * 100 / total) if total else 100
        detail = f"{index + 1} of {total} · {asset.name}"

        # Forward the per-asset job's own progress calls as *this* job's position rather
        # than dropping them. progress() is the cancellation checkpoint, and an interview
        # with three thousand segments is minutes of work — a cancel has to be able to
        # land inside one asset, not only between two.
        def inner(_stage, _pct, _detail="", *, stage=stage, pct=pct, detail=detail):
            progress(stage, pct, detail)

        progress(stage, pct, detail)

        try:
            vectors += embed.run(session, asset, inner)
            embedded += 1
            consecutive = 0
        except EmbeddingUnavailable:
            # The provider went away mid-run. Nothing further will succeed.
            raise
        except Exception as exc:  # noqa: BLE001 - one bad asset must not end the run
            failed += 1
            consecutive += 1
            logger.warning("Could not embed asset %s during backfill", asset_id, exc_info=True)
            if consecutive >= CONSECUTIVE_FAILURE_LIMIT:
                raise RuntimeError(
                    f"Stopped after {consecutive} assets failed in a row. "
                    f"The last error was: {exc}"
                ) from exc

    return BackfillResult(embedded=embedded, vectors=vectors, failed=failed)
