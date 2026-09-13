"""Fusing keyword and semantic results.

Two retrievers that fail in opposite directions. Keyword search nails an exact phrase
and returns nothing when the user's words are not the speaker's — which is the common
case for half-remembered material. Semantic search finds the meaning and is vague about
exact names, quotes and numbers.

They are combined with Reciprocal Rank Fusion, which scores a result by where it placed
in each list rather than by either list's score:

    score(d) = Σ  1 / (k + rank_i(d))

That matters because the two scores are not comparable and never will be: bm25 is an
unbounded relevance number whose scale depends on the corpus, cosine similarity is
bounded in [-1, 1] and compresses badly at the top. Any attempt to weight them directly
means tuning a constant against one library and watching it be wrong for the next.
Ranks have no units, so there is nothing to tune.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

# The constant from the original RRF paper (Cormack et al., 2009). It damps the
# difference between the top few ranks, so one retriever being confidently first does
# not automatically win — which is the entire point of fusing rather than picking.
RRF_K = 60


@dataclass
class Candidate:
    """One retrievable thing: an asset, or a moment inside one."""

    asset_id: str
    segment_id: Optional[str] = None
    start_time: Optional[float] = None
    snippet: str = ""
    # Which retrievers found it, for explaining a result rather than only ranking it.
    sources: set[str] = field(default_factory=set)
    score: float = 0.0

    @property
    def key(self) -> tuple[str, Optional[str]]:
        return (self.asset_id, self.segment_id)


@dataclass
class FusedAsset:
    """An asset, and the best moment inside it that matched."""

    asset_id: str
    score: float
    snippet: str
    start_time: Optional[float]
    segment_id: Optional[str]
    sources: set[str]
    # How many other moments in this asset also matched. A ninety-minute interview that
    # returns its single best moment is useful; one that floods the page with twenty of
    # its own moments is not.
    other_matches: int = 0


def fuse(ranked_lists: dict[str, Sequence[Candidate]], *, k: int = RRF_K) -> list[Candidate]:
    """Combine ranked lists into one, best first.

    Each list is assumed already ordered best-first by its own retriever; only the
    ordering is used, never the scores.
    """
    merged: dict[tuple[str, Optional[str]], Candidate] = {}

    for source, candidates in ranked_lists.items():
        for rank, candidate in enumerate(candidates, start=1):
            existing = merged.get(candidate.key)
            if existing is None:
                existing = Candidate(
                    asset_id=candidate.asset_id,
                    segment_id=candidate.segment_id,
                    start_time=candidate.start_time,
                    snippet=candidate.snippet,
                )
                merged[candidate.key] = existing

            # Prefer a snippet that actually has text: the semantic retriever has none
            # to give, so a hit found by both should show the keyword one's excerpt.
            if candidate.snippet and not existing.snippet:
                existing.snippet = candidate.snippet
            if existing.start_time is None and candidate.start_time is not None:
                existing.start_time = candidate.start_time

            existing.sources.add(source)
            existing.score += 1.0 / (k + rank)

    return sorted(merged.values(), key=lambda c: (-c.score, c.asset_id))


def collapse_to_assets(candidates: Sequence[Candidate]) -> list[FusedAsset]:
    """One row per asset, keeping its best-scoring moment.

    A library search answers "which of my things is this in", and the moment is how you
    get to it. Returning every matching moment separately would let one long interview
    fill the page and push every other asset off it.
    """
    best: dict[str, FusedAsset] = {}

    for candidate in candidates:
        existing = best.get(candidate.asset_id)
        if existing is None:
            best[candidate.asset_id] = FusedAsset(
                asset_id=candidate.asset_id,
                score=candidate.score,
                snippet=candidate.snippet,
                start_time=candidate.start_time,
                segment_id=candidate.segment_id,
                sources=set(candidate.sources),
            )
            continue

        existing.other_matches += 1
        existing.sources |= candidate.sources
        # The asset's rank is driven by its best moment, not the sum of its moments —
        # otherwise a long file wins by volume rather than by relevance.
        if candidate.score > existing.score:
            existing.score = candidate.score
            existing.snippet = candidate.snippet
            existing.start_time = candidate.start_time
            existing.segment_id = candidate.segment_id

    return sorted(best.values(), key=lambda a: (-a.score, a.asset_id))
