"""Keyword search over asset metadata and transcript segments."""

import pytest

from app.models.asset import Asset
from app.models.transcript import TranscriptSegment
from app.search import fts


def make_asset(session, **overrides) -> Asset:
    asset = Asset(
        user_id=overrides.pop("user_id", "u1"),
        name=overrides.pop("name", "Untitled"),
        asset_type="video",
        source="local_upload",
        **overrides,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def make_segments(session, asset, *texts):
    segments = []
    for index, (text_, start) in enumerate(texts):
        segment = TranscriptSegment(
            asset_id=asset.id,
            user_id=asset.user_id,
            idx=index,
            text=text_,
            start_time=start,
            end_time=start + 5,
        )
        session.add(segment)
        segments.append(segment)
    session.commit()
    fts.index_segments(session, asset.id, segments)
    return segments


# ─── query building: a search box is not a query language ────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        'unbalanced "quote',
        "trailing *",
        "NEAR(",
        "a AND OR b",
        "^caret",
        "col:value",
        "-minus",
        "((((",
    ],
)
def test_operator_soup_does_not_raise(session, raw):
    """FTS5 treats these as syntax and raises. A user typing into a box is not writing
    a query language, so every token is quoted and the operators are dropped."""
    asset = make_asset(session, name="Something")
    fts.index_asset(session, asset)

    fts.search_assets(session, "u1", raw)  # must not raise


def test_an_empty_query_matches_nothing(session):
    assert fts.build_match_query("") == ""
    assert fts.build_match_query("   ") == ""
    assert fts.build_match_query("!!!") == ""
    assert fts.search_assets(session, "u1", "") == []


def test_the_last_token_is_a_prefix(session):
    """So results narrow while typing rather than vanishing between whole words."""
    assert fts.build_match_query("nano weap") == '"nano" "weap"*'
    assert fts.build_match_query("nano weap", prefix_last=False) == '"nano" "weap"'


# ─── asset metadata ──────────────────────────────────────────────────────────


def test_finds_an_asset_by_name(session):
    asset = make_asset(session, name="Giordano interview")
    fts.index_asset(session, asset)

    hits = fts.search_assets(session, "u1", "giordano")
    assert [h.asset_id for h in hits] == [asset.id]


def test_finds_an_asset_by_description(session):
    asset = make_asset(session, name="clip_0042", description="Testimony on aerosol dispersal")
    fts.index_asset(session, asset)

    assert [h.asset_id for h in fts.search_assets(session, "u1", "aerosol")] == [asset.id]


def test_search_is_stemmed(session):
    """"deploy" must find "deploying" — that is what someone half-remembering types."""
    asset = make_asset(session, name="Deploying nano weapons")
    fts.index_asset(session, asset)

    assert fts.search_assets(session, "u1", "deploy")


def test_search_is_case_and_accent_folded(session):
    asset = make_asset(session, name="Café Discussion")
    fts.index_asset(session, asset)

    assert fts.search_assets(session, "u1", "CAFE")


def test_results_are_scoped_to_the_owner(session):
    mine = make_asset(session, name="Giordano interview", user_id="u1")
    theirs = make_asset(session, name="Giordano interview", user_id="u2")
    fts.index_asset(session, mine)
    fts.index_asset(session, theirs)

    assert [h.asset_id for h in fts.search_assets(session, "u1", "giordano")] == [mine.id]


def test_reindexing_replaces_rather_than_duplicates(session):
    asset = make_asset(session, name="First name")
    fts.index_asset(session, asset)

    asset.name = "Second name"
    fts.index_asset(session, asset)

    assert len(fts.search_assets(session, "u1", "name")) == 1
    assert not fts.search_assets(session, "u1", "first")
    assert fts.search_assets(session, "u1", "second")


def test_removing_an_asset_clears_it(session):
    asset = make_asset(session, name="Giordano interview")
    fts.index_asset(session, asset)
    fts.remove_asset(session, asset.id)

    assert fts.search_assets(session, "u1", "giordano") == []


# ─── transcript segments: the timestamp is the point ─────────────────────────


def test_a_spoken_hit_carries_its_timestamp(session):
    """FR 10.1.4. An asset id alone leaves the user scrubbing a ninety-minute file."""
    asset = make_asset(session, name="Giordano interview")
    make_segments(
        session,
        asset,
        ("Good afternoon and welcome.", 0.0),
        ("We are looking at deploying nano weapons via aerosol dispersion.", 412.0),
    )

    hits = fts.search_segments(session, "u1", "nano weapons")
    assert len(hits) == 1
    assert hits[0].asset_id == asset.id
    assert hits[0].start_time == 412.0


def test_the_snippet_marks_the_match(session):
    asset = make_asset(session, name="Interview")
    make_segments(session, asset, ("We are deploying nano weapons via aerosol.", 412.0))

    hit = fts.search_segments(session, "u1", "nano")[0]
    assert "«nano»" in hit.snippet


def test_segments_are_scoped_to_the_owner(session):
    mine = make_asset(session, name="Mine", user_id="u1")
    theirs = make_asset(session, name="Theirs", user_id="u2")
    make_segments(session, mine, ("nano weapons", 1.0))
    make_segments(session, theirs, ("nano weapons", 1.0))

    assert [h.asset_id for h in fts.search_segments(session, "u1", "nano")] == [mine.id]


def test_reindexing_a_transcript_replaces_it(session):
    """A re-run changes the segment set's shape, so reconciling row by row would leave
    orphans from the previous run."""
    asset = make_asset(session, name="Interview")
    make_segments(session, asset, ("original wording here", 1.0), ("second line", 9.0))

    make_segments(session, asset, ("replaced wording here", 1.0))

    assert not fts.search_segments(session, "u1", "original")
    assert fts.search_segments(session, "u1", "replaced")
    assert not fts.search_segments(session, "u1", "second")


def test_blank_segments_are_not_indexed(session):
    """Deepgram emits whitespace-only utterances for silence."""
    asset = make_asset(session, name="Interview")
    count = fts.index_segments(
        session,
        asset.id,
        [
            TranscriptSegment(asset_id=asset.id, user_id="u1", idx=0, text="   ", start_time=1.0),
            TranscriptSegment(asset_id=asset.id, user_id="u1", idx=1, text="real words", start_time=2.0),
        ],
    )
    assert count == 1


def test_deleting_an_asset_clears_its_transcript_too(session):
    asset = make_asset(session, name="Interview")
    make_segments(session, asset, ("nano weapons", 412.0))

    fts.remove_asset(session, asset.id)
    assert fts.search_segments(session, "u1", "nano") == []


# ─── ranking ─────────────────────────────────────────────────────────────────


def test_a_closer_match_ranks_higher(session):
    """bm25 ordering: the segment that is mostly the query beats one that merely
    contains it among much else."""
    asset = make_asset(session, name="Interview")
    make_segments(
        session,
        asset,
        ("nano weapons", 10.0),
        (
            "There are many topics today including agriculture, shipping, taxation, "
            "energy policy, nano weapons, education and the weather.",
            20.0,
        ),
    )

    hits = fts.search_segments(session, "u1", "nano weapons")
    assert len(hits) == 2
    assert hits[0].start_time == 10.0, "the denser match should rank first"
