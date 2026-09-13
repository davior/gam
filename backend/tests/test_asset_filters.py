"""Narrowing the library, and doing it in a bounded number of queries."""

from datetime import timedelta

from sqlalchemy import event

from app.clock import utcnow
from app.models.asset import Asset
from app.search import fts

TEST_USER = "user-under-test"


def make_asset(session, name, **extra):
    asset = Asset(
        user_id=TEST_USER,
        name=name,
        asset_type=extra.pop("asset_type", "video"),
        source=extra.pop("source", "local_upload"),
        **extra,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    fts.index_asset(session, asset)
    return asset


def names(body):
    return sorted(row["name"] for row in body["data"])


# ─── tags ────────────────────────────────────────────────────────────────────


def test_two_tags_narrows_rather_than_widens(library, session):
    """AND, not OR. Picking a second filter must never return more than the first did —
    that is the one behaviour that makes a filter chip feel broken."""
    both = make_asset(session, "Both")
    one = make_asset(session, "Only interview")
    make_asset(session, "Neither")

    library.post(f"/api/assets/{both.id}/tags", json={"names": ["interview", "2024"]})
    library.post(f"/api/assets/{one.id}/tags", json={"names": ["interview"]})

    single = library.get("/api/assets", params={"tag": ["interview"]}).json()
    assert names(single) == ["Both", "Only interview"]

    pair = library.get("/api/assets", params={"tag": ["interview", "2024"]}).json()
    assert names(pair) == ["Both"]
    assert pair["total"] == 1


def test_an_unknown_tag_matches_nothing(library, session):
    make_asset(session, "Something")
    body = library.get("/api/assets", params={"tag": ["never-used"]}).json()
    assert body["data"] == []
    assert body["total"] == 0


def test_tags_come_back_on_each_asset(library, session):
    asset = make_asset(session, "Tagged")
    library.post(f"/api/assets/{asset.id}/tags", json={"names": ["archive", "NATO"]})

    row = library.get("/api/assets").json()["data"][0]
    assert sorted(t["name"] for t in row["tags"]) == ["NATO", "archive"]


# ─── the other filters ───────────────────────────────────────────────────────


def test_filtering_by_source(library, session):
    make_asset(session, "Uploaded", source="local_upload")
    make_asset(session, "Generated", source="ai_generated")

    body = library.get("/api/assets", params={"source": "ai_generated"}).json()
    assert names(body) == ["Generated"]


def test_filtering_by_duration(library, session):
    make_asset(session, "Short", duration_seconds=30.0)
    make_asset(session, "Long", duration_seconds=3600.0)
    make_asset(session, "Untimed")  # duration is NULL

    over_a_minute = library.get("/api/assets", params={"min_duration": 60}).json()
    assert names(over_a_minute) == ["Long"]

    under_a_minute = library.get("/api/assets", params={"max_duration": 60}).json()
    assert names(under_a_minute) == ["Short"]

    # An asset with no duration is excluded by a duration filter rather than treated as
    # zero — "shorter than a minute" should not surface every PDF in the library.
    assert "Untimed" not in names(over_a_minute) + names(under_a_minute)


def test_an_inverted_duration_range_is_rejected(library):
    response = library.get("/api/assets", params={"min_duration": 100, "max_duration": 10})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "bad_request"


def test_filtering_by_upload_date(library, session):
    old = make_asset(session, "Old")
    recent = make_asset(session, "Recent")

    old.upload_date = utcnow() - timedelta(days=30)
    session.add(old)
    session.commit()

    cutoff = (utcnow() - timedelta(days=1)).isoformat()
    body = library.get("/api/assets", params={"uploaded_after": cutoff}).json()
    assert names(body) == ["Recent"]
    assert recent.id == body["data"][0]["id"]


def test_filters_combine(library, session):
    wanted = make_asset(session, "Wanted", duration_seconds=120.0, source="local_upload")
    make_asset(session, "Wrong duration", duration_seconds=5.0, source="local_upload")
    make_asset(session, "Wrong source", duration_seconds=120.0, source="ai_generated")

    library.post(f"/api/assets/{wanted.id}/tags", json={"names": ["keep"]})

    body = library.get(
        "/api/assets",
        params={"tag": ["keep"], "min_duration": 60, "source": "local_upload"},
    ).json()
    assert names(body) == ["Wanted"]


def test_total_reflects_the_filter_not_the_library(library, session):
    """`total` drives the "load more" logic, so a total that ignores the filter makes
    the grid ask for pages that do not exist."""
    keep = make_asset(session, "Keep")
    for i in range(5):
        make_asset(session, f"Other {i}")
    library.post(f"/api/assets/{keep.id}/tags", json={"names": ["keep"]})

    body = library.get("/api/assets", params={"tag": ["keep"]}).json()
    assert body["total"] == 1


# ─── the N+1 ─────────────────────────────────────────────────────────────────


def test_listing_does_not_run_a_query_per_asset(library, session, engine):
    """An N+1 here is invisible on a development library of five and miserable on a real
    one, so it is asserted rather than trusted.

    The bound is on *growth*, not on an exact count — pinning the precise number would
    make this fail every time an unrelated query was added, and then it would be deleted.
    What matters is that twenty assets do not cost four times what five do.
    """

    created = 0

    def grow_to(total):
        nonlocal created
        while created < total:
            asset = make_asset(session, f"Asset {created}")
            library.post(
                f"/api/assets/{asset.id}/tags",
                json={"names": [f"tag{created}", "shared"]},
            )
            created += 1

        counted = []
        listener = lambda *args, **kwargs: counted.append(1)  # noqa: E731
        event.listen(engine, "before_cursor_execute", listener)
        try:
            body = library.get("/api/assets", params={"limit": 200}).json()
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        assert len(body["data"]) == total
        return len(counted)

    five = grow_to(5)
    twenty = grow_to(20)

    assert twenty <= five + 2, (
        f"query count grew with the page: {five} queries for 5 assets, {twenty} for 20. "
        "Tags are meant to be batch-loaded in one query."
    )
