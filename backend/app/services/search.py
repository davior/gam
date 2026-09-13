"""Running a search across both retrievers and returning something readable."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, col, select

from app.embeddings import EmbeddingError, build_embedder
from app.models.asset import Asset
from app.models.embedding import OWNER_SEGMENT
from app.models.transcript import TranscriptSegment
from app.search import fts, vectors
from app.search.hybrid import Candidate, FusedAsset, collapse_to_assets, fuse

logger = logging.getLogger(__name__)

# How deep each retriever goes before fusion. Wider than the page the user sees, because
# fusion can promote something that placed tenth in one list and second in the other —
# and it cannot promote what it was never handed.
RETRIEVER_DEPTH = 50

SOURCE_KEYWORD = "keyword"
SOURCE_SEMANTIC = "semantic"


@dataclass
class SearchOutcome:
    assets: list[FusedAsset]
    # Whether the semantic half actually ran. The UI says so, because "no results" means
    # something different when only half the search happened.
    semantic_ran: bool
    semantic_error: Optional[str] = None


def search(
    session: Session,
    user_id: str,
    query: str,
    *,
    limit: int = 30,
    asset_type: Optional[str] = None,
) -> SearchOutcome:
    query = (query or "").strip()
    if not query:
        return SearchOutcome(assets=[], semantic_ran=False)

    lists: dict[str, list[Candidate]] = {SOURCE_KEYWORD: _keyword_candidates(session, user_id, query)}

    semantic_ran = False
    semantic_error: Optional[str] = None
    try:
        semantic = _semantic_candidates(session, user_id, query)
        if semantic is not None:
            lists[SOURCE_SEMANTIC] = semantic
            semantic_ran = True
    except EmbeddingError as exc:
        # A provider being down degrades the search to keyword-only rather than failing
        # it. Half a search beats an error page.
        semantic_error = str(exc)
        logger.info("Semantic search unavailable: %s", exc)

    fused = collapse_to_assets(fuse(lists))

    if asset_type:
        allowed = _asset_ids_of_type(session, user_id, asset_type)
        fused = [a for a in fused if a.asset_id in allowed]

    return SearchOutcome(
        assets=fused[:limit], semantic_ran=semantic_ran, semantic_error=semantic_error
    )


def _keyword_candidates(session: Session, user_id: str, query: str) -> list[Candidate]:
    """Metadata hits and spoken hits, interleaved by their own rank.

    Two FTS queries rather than one because they are separate indexes; their results are
    concatenated with segment hits first, since a hit that carries a timestamp is more
    useful than one that only names a file.
    """
    segment_hits = fts.search_segments(session, user_id, query, limit=RETRIEVER_DEPTH)
    asset_hits = fts.search_assets(session, user_id, query, limit=RETRIEVER_DEPTH)

    candidates = [
        Candidate(
            asset_id=hit.asset_id,
            segment_id=hit.segment_id,
            start_time=hit.start_time,
            snippet=hit.snippet,
        )
        for hit in segment_hits
    ]
    candidates.extend(
        Candidate(asset_id=hit.asset_id, snippet=hit.snippet) for hit in asset_hits
    )
    return candidates


def _semantic_candidates(session: Session, user_id: str, query: str) -> Optional[list[Candidate]]:
    """None when no provider is configured — which is not an error, just a library that
    has not been set up for it yet."""
    embedder = build_embedder(session, user_id)
    if embedder is None:
        return None

    query_vector = embedder.embed([query])
    if not query_vector:
        return None

    hits = vectors.search(
        session, user_id, query_vector[0], model=embedder.model, limit=RETRIEVER_DEPTH
    )
    if not hits:
        return []

    # Segment hits need their text to be shown; one query for all of them rather than
    # one per hit.
    segment_ids = [h.owner_id for h in hits if h.owner_kind == OWNER_SEGMENT]
    segments: dict[str, TranscriptSegment] = {}
    if segment_ids:
        rows = session.exec(
            select(TranscriptSegment).where(col(TranscriptSegment.id).in_(segment_ids))
        ).all()
        segments = {row.id: row for row in rows}

    candidates: list[Candidate] = []
    for hit in hits:
        if hit.owner_kind == OWNER_SEGMENT:
            segment = segments.get(hit.owner_id)
            if segment is None:
                # An embedding whose segment was deleted; skip rather than show a blank.
                continue
            candidates.append(
                Candidate(
                    asset_id=hit.asset_id,
                    segment_id=segment.id,
                    start_time=segment.start_time,
                    snippet=segment.text,
                )
            )
        else:
            candidates.append(Candidate(asset_id=hit.asset_id))

    return candidates


def _asset_ids_of_type(session: Session, user_id: str, asset_type: str) -> set[str]:
    rows = session.exec(
        select(Asset.id).where(Asset.user_id == user_id, Asset.asset_type == asset_type)
    ).all()
    return set(rows)


def load_assets(session: Session, user_id: str, asset_ids: list[str]) -> dict[str, Asset]:
    if not asset_ids:
        return {}
    rows = session.exec(
        select(Asset).where(Asset.user_id == user_id, col(Asset.id).in_(asset_ids))
    ).all()
    return {row.id: row for row in rows}
