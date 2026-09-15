"""Tags, categories, and the seam between them and search."""

import pytest
from sqlmodel import select

from app.models.asset import Asset
from app.models.tag import AssetTag, Tag, TagCategory
from app.search import fts
from app.services import tags as service

TEST_USER = "user-under-test"


def make_asset(session, name="Giordano interview", **extra):
    asset = Asset(
        user_id=TEST_USER, name=name, asset_type="video", source="local_upload", **extra
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    fts.index_asset(session, asset)
    return asset


def add_tags(client, asset_id, *names):
    return client.post(f"/api/assets/{asset_id}/tags", json={"names": list(names)})


# ─── the seam: a tag has to make the asset findable ──────────────────────────


def test_tagging_an_asset_makes_it_findable_by_that_tag(library, session):
    """The whole reason M3 and M5 touch each other.

    `asset_fts` has carried a `tags_text` column since M5, filled by nothing. If the
    re-index after a tag write is missed, everything else here still passes — the tag
    attaches, the API returns it, the UI shows it — and the only symptom is that
    searching for it finds nothing. So this asserts through /api/search rather than
    through the tag API, because the tag API cannot tell the difference.
    """
    asset = make_asset(session)

    # Prove the word is not already findable, or the assertion below proves nothing.
    before = library.get("/api/search", params={"q": "declassified"}).json()
    assert before["data"] == []

    add_tags(library, asset.id, "declassified")

    after = library.get("/api/search", params={"q": "declassified"}).json()
    assert [row["asset"]["id"] for row in after["data"]] == [asset.id]


def test_removing_a_tag_stops_it_answering_searches(library, session):
    """The other half. A detached tag left in the index keeps answering for a word the
    library no longer contains, which is worse than never having indexed it."""
    asset = make_asset(session)
    add_tags(library, asset.id, "declassified")

    tag = session.exec(select(Tag).where(Tag.name == "declassified")).one()
    library.delete(f"/api/assets/{asset.id}/tags/{tag.id}")

    found = library.get("/api/search", params={"q": "declassified"}).json()
    assert found["data"] == []


def test_deleting_a_tag_reindexes_everything_that_carried_it(library, session):
    asset_a = make_asset(session, name="One")
    asset_b = make_asset(session, name="Two")
    add_tags(library, asset_a.id, "surveillance")
    add_tags(library, asset_b.id, "surveillance")

    tag = session.exec(select(Tag).where(Tag.name == "surveillance")).one()
    assert library.delete(f"/api/tags/{tag.id}").status_code == 204

    assert library.get("/api/search", params={"q": "surveillance"}).json()["data"] == []


def test_renaming_a_tag_moves_the_searchable_word(library, session):
    """A rename changes what every tagged asset is findable by. Both directions matter:
    the new name has to start working and the old one has to stop."""
    asset = make_asset(session)
    add_tags(library, asset.id, "psyop")
    tag = session.exec(select(Tag).where(Tag.name == "psyop")).one()

    library.patch(f"/api/tags/{tag.id}", json={"name": "influence operation"})

    assert library.get("/api/search", params={"q": "psyop"}).json()["data"] == []
    found = library.get("/api/search", params={"q": "influence"}).json()
    assert [row["asset"]["id"] for row in found["data"]] == [asset.id]


# ─── case ────────────────────────────────────────────────────────────────────


def test_the_case_you_typed_is_the_case_you_get_back(library, session):
    asset = make_asset(session)
    body = add_tags(library, asset.id, "NATO").json()
    assert [t["name"] for t in body["data"]] == ["NATO"]


def test_a_differently_cased_tag_attaches_the_existing_one(library, session):
    """Otherwise the library silently splits in two: two tags that look identical in
    every list, each holding half the assets."""
    asset_a = make_asset(session, name="One")
    asset_b = make_asset(session, name="Two")

    add_tags(library, asset_a.id, "NATO")
    add_tags(library, asset_b.id, "nato")

    tags = session.exec(select(Tag)).all()
    assert len(tags) == 1
    assert tags[0].name == "NATO"  # the first spelling wins, and keeps its case


def test_whitespace_is_collapsed_not_preserved(library, session):
    asset = make_asset(session)
    body = add_tags(library, asset.id, "  klaus   schwab  ").json()
    assert [t["name"] for t in body["data"]] == ["klaus schwab"]


def test_a_blank_tag_is_refused(library):
    assert library.post("/api/tags", json={"name": "   "}).status_code == 422


# ─── categories and the recursive walk ───────────────────────────────────────


def make_category(client, name, parent=None):
    return client.post(
        "/api/tags/categories", json={"name": name, "parent_category_id": parent}
    ).json()["data"]


def test_filtering_by_a_parent_category_finds_its_grandchildren(library, session):
    """If nesting does not descend, it is decoration.

    The decoy is load-bearing. FastAPI drops unknown query parameters silently, so a
    version of this test with only the tagged asset in the library passed before the
    filter existed at all — the endpoint ignored `category_id` and returned everything,
    which was the one asset being looked for. An untagged second asset is what makes the
    assertion about filtering rather than about counting to one.
    """
    people = make_category(library, "People")
    scientists = make_category(library, "Scientists", people["id"])
    neuro = make_category(library, "Neuroscientists", scientists["id"])

    asset = make_asset(session, name="Giordano interview")
    make_asset(session, name="Unrelated holiday video")

    tag = library.post("/api/tags", json={"name": "giordano", "category_id": neuro["id"]}).json()
    library.post(f"/api/assets/{asset.id}/tags", json={"names": ["giordano"]})

    found = library.get("/api/assets", params={"category_id": people["id"]}).json()
    assert [row["id"] for row in found["data"]] == [asset.id]
    assert tag["data"]["category_id"] == neuro["id"]


def test_a_category_cannot_be_moved_inside_itself(library):
    parent = make_category(library, "People")
    child = make_category(library, "Scientists", parent["id"])

    # Direct: A under A.
    assert (
        library.patch(
            f"/api/tags/categories/{parent['id']}", json={"parent_category_id": parent["id"]}
        ).status_code
        == 400
    )
    # Indirect: the parent under its own child.
    response = library.patch(
        f"/api/tags/categories/{parent['id']}", json={"parent_category_id": child["id"]}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "bad_request"


def test_a_cycle_forced_into_the_database_still_terminates(library, session):
    """The belt, not the braces.

    `would_create_cycle` refuses the write, so this cannot happen through the API. But a
    recursive CTE meeting a loop does not return a wrong answer, it never returns — it
    hangs the worker thread, and that is not a failure mode to leave resting on a single
    guard. So the cycle is written directly to the database, past the guard, and the walk
    still has to finish. `UNION` rather than `UNION ALL` is what makes it.
    """
    a = make_category(library, "A")
    b = make_category(library, "B", a["id"])

    row_a = session.get(TagCategory, a["id"])
    row_a.parent_category_id = b["id"]  # A -> B -> A
    session.add(row_a)
    session.commit()

    ids = service.descendant_category_ids(session, TEST_USER, a["id"])
    assert set(ids) == {a["id"], b["id"]}


def test_deleting_a_category_keeps_its_tags(library, session):
    """A tag is what the user curated. The category is only where they filed it, so
    tidying the shelves must not burn the books."""
    parent = make_category(library, "People")
    child = make_category(library, "Scientists", parent["id"])
    library.post("/api/tags", json={"name": "giordano", "category_id": child["id"]})

    assert library.delete(f"/api/tags/categories/{parent['id']}").status_code == 204

    tag = session.exec(select(Tag).where(Tag.name == "giordano")).one()
    assert tag is not None
    surviving_child = session.get(TagCategory, child["id"])
    assert surviving_child is not None
    assert surviving_child.parent_category_id is None  # lifted to the top level


# ─── bulk ────────────────────────────────────────────────────────────────────


def test_bulk_tagging_applies_and_reindexes_every_asset(library, session):
    assets = [make_asset(session, name=f"Clip {i}") for i in range(5)]

    result = library.post(
        "/api/assets/tags/bulk",
        json={"asset_ids": [a.id for a in assets], "add": ["archive"]},
    ).json()
    assert result["data"]["updated"] == 5

    found = library.get("/api/search", params={"q": "archive"}).json()
    assert len(found["data"]) == 5


def test_bulk_remove_takes_it_off_every_selected_asset(library, session):
    assets = [make_asset(session, name=f"Clip {i}") for i in range(3)]
    ids = [a.id for a in assets]
    library.post("/api/assets/tags/bulk", json={"asset_ids": ids, "add": ["archive"]})
    tag = session.exec(select(Tag).where(Tag.name == "archive")).one()

    library.post(
        "/api/assets/tags/bulk", json={"asset_ids": ids[:2], "remove": [tag.id]}
    )

    remaining = library.get("/api/search", params={"q": "archive"}).json()
    assert [row["asset"]["id"] for row in remaining["data"]] == [ids[2]]


def test_bulk_tagging_ignores_assets_you_do_not_own(library, session):
    """A selection arrives from the client and is not evidence of anything."""
    mine = make_asset(session)
    theirs = Asset(
        user_id="somebody-else", name="Not yours", asset_type="video", source="local_upload"
    )
    session.add(theirs)
    session.commit()
    session.refresh(theirs)

    result = library.post(
        "/api/assets/tags/bulk",
        json={"asset_ids": [mine.id, theirs.id], "add": ["archive"]},
    ).json()

    assert result["data"]["updated"] == 1
    assert session.exec(select(AssetTag).where(AssetTag.asset_id == theirs.id)).first() is None


# ─── scoping ─────────────────────────────────────────────────────────────────


def test_another_users_tags_are_invisible(library, session):
    session.add(Tag(user_id="somebody-else", name="theirs"))
    session.commit()

    body = library.get("/api/tags").json()
    assert body["data"] == []


def test_you_cannot_attach_someone_elses_tag(library, session):
    asset = make_asset(session)
    theirs = Tag(user_id="somebody-else", name="theirs")
    session.add(theirs)
    session.commit()
    session.refresh(theirs)

    assert library.delete(f"/api/assets/{asset.id}/tags/{theirs.id}").status_code == 404


def test_tags_require_authentication(client):
    assert client.get("/api/tags").status_code == 401


# ─── counts ──────────────────────────────────────────────────────────────────


def test_the_tag_list_reports_how_many_assets_use_each_tag(library, session):
    a, b = make_asset(session, name="One"), make_asset(session, name="Two")
    add_tags(library, a.id, "archive", "solo")
    add_tags(library, b.id, "archive")

    counts = {t["name"]: t["asset_count"] for t in library.get("/api/tags").json()["data"]}
    assert counts == {"archive": 2, "solo": 1}


def test_attaching_the_same_tag_twice_is_not_an_error(library, session):
    asset = make_asset(session)
    add_tags(library, asset.id, "archive")
    body = add_tags(library, asset.id, "archive").json()
    assert [t["name"] for t in body["data"]] == ["archive"]


# ─── tags on the single-asset responses ──────────────────────────────────────
#
# Both of these shipped returning an empty tag list. `to_read_model` defaults the
# argument, so omitting it does not fail — it quietly answers "this asset has no tags",
# and the frontend store believes the answer. Nothing caught it because `assetsApi.get`
# had no caller at all until enrichment needed one, and the PATCH case only shows up as
# tags vanishing from the grid after a rename, until the next reload.


def test_reading_one_asset_carries_its_tags(library, session):
    asset = make_asset(session)
    add_tags(library, asset.id, "NATO")

    body = library.get(f"/api/assets/{asset.id}").json()["data"]

    assert [t["name"] for t in body["tags"]] == ["NATO"]


def test_editing_an_asset_does_not_drop_its_tags(library, session):
    """The store replaces its copy with this response, so an empty list here is an
    asset whose tags disappear from the grid."""
    asset = make_asset(session)
    add_tags(library, asset.id, "NATO")

    body = library.patch(
        f"/api/assets/{asset.id}", json={"description": "renamed something"}
    ).json()["data"]

    assert [t["name"] for t in body["tags"]] == ["NATO"]
