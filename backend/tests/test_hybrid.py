"""Reciprocal rank fusion, and collapsing moments to assets.

Pure functions, so these assert the ranking behaviour directly rather than through a
search that would also involve two indexes and a network call.
"""

from app.search.hybrid import RRF_K, Candidate, collapse_to_assets, fuse


def c(asset_id, segment_id=None, snippet="", start_time=None):
    return Candidate(
        asset_id=asset_id, segment_id=segment_id, snippet=snippet, start_time=start_time
    )


def test_a_result_both_retrievers_found_beats_one_either_found_alone():
    """The whole reason to fuse. Keyword nails exact phrases and misses paraphrase;
    semantic finds meaning and is vague about names. Agreement is the strong signal."""
    fused = fuse(
        {
            "keyword": [c("only-keyword"), c("both")],
            "semantic": [c("only-semantic"), c("both")],
        }
    )

    assert fused[0].asset_id == "both"
    assert fused[0].sources == {"keyword", "semantic"}


def test_rank_is_what_counts_not_score():
    """bm25 is unbounded and corpus-dependent; cosine is bounded and compresses at the
    top. They are not comparable, so only position is used."""
    first, second = c("a"), c("b")
    fused = fuse({"keyword": [first, second]})

    assert fused[0].asset_id == "a"
    assert fused[0].score == 1.0 / (RRF_K + 1)
    assert fused[1].score == 1.0 / (RRF_K + 2)


def test_one_retriever_alone_still_returns_results():
    """No embedding provider configured is the normal state of a new library."""
    fused = fuse({"keyword": [c("a"), c("b")]})
    assert [f.asset_id for f in fused] == ["a", "b"]


def test_an_empty_list_contributes_nothing():
    fused = fuse({"keyword": [c("a")], "semantic": []})
    assert [f.asset_id for f in fused] == ["a"]


def test_fusing_nothing_returns_nothing():
    assert fuse({}) == []
    assert fuse({"keyword": []}) == []


def test_a_keyword_snippet_survives_a_semantic_match():
    """The semantic retriever has no excerpt to give, so a hit found by both should
    still show the one with the highlighted words."""
    fused = fuse(
        {
            "keyword": [c("a", "s1", snippet="«nano» weapons")],
            "semantic": [c("a", "s1", snippet="")],
        }
    )

    assert fused[0].snippet == "«nano» weapons"


def test_a_timestamp_survives_from_whichever_retriever_had_it():
    fused = fuse(
        {
            "keyword": [c("a", "s1", start_time=None)],
            "semantic": [c("a", "s1", start_time=412.0)],
        }
    )
    assert fused[0].start_time == 412.0


def test_moments_and_the_asset_itself_are_distinct_candidates():
    """A file whose name matches and which also says the words twice is three hits, not
    one — until they are collapsed."""
    fused = fuse({"keyword": [c("a"), c("a", "s1"), c("a", "s2")]})
    assert len(fused) == 3


# ─── collapsing ──────────────────────────────────────────────────────────────


def test_collapsing_keeps_one_row_per_asset():
    """A ninety-minute interview returning its best moment is useful; the same one
    flooding the page with twenty of its own moments is not."""
    fused = fuse({"keyword": [c("a", "s1"), c("a", "s2"), c("b", "s3")]})
    collapsed = collapse_to_assets(fused)

    assert [a.asset_id for a in collapsed] == ["a", "b"]


def test_the_kept_moment_is_the_best_one():
    fused = fuse(
        {
            "keyword": [c("a", "weak", start_time=10.0), c("a", "strong", start_time=412.0)],
            "semantic": [c("a", "strong", start_time=412.0)],
        }
    )
    collapsed = collapse_to_assets(fused)

    assert collapsed[0].segment_id == "strong"
    assert collapsed[0].start_time == 412.0


def test_other_matches_are_counted_not_discarded():
    """So the UI can say "and 4 more moments" rather than silently hiding them."""
    fused = fuse({"keyword": [c("a", "s1"), c("a", "s2"), c("a", "s3")]})
    collapsed = collapse_to_assets(fused)

    assert collapsed[0].other_matches == 2


def test_an_asset_does_not_win_by_volume():
    """Ranking on the best moment, not the sum. Otherwise a long file outranks a short
    one purely by having more chances to match."""
    fused = fuse(
        {
            "keyword": [
                c("short", "best"),       # rank 1 — the strongest single moment
                c("long", "a"),
                c("long", "b"),
                c("long", "c"),
                c("long", "d"),
            ]
        }
    )
    collapsed = collapse_to_assets(fused)

    assert collapsed[0].asset_id == "short"


def test_sources_are_unioned_across_collapsed_moments():
    fused = fuse(
        {
            "keyword": [c("a", "s1")],
            "semantic": [c("a", "s2")],
        }
    )
    collapsed = collapse_to_assets(fused)

    assert collapsed[0].sources == {"keyword", "semantic"}
