"""The assistant organising the tag vocabulary: groups, renames, merges, deletes.

As with everything the assistant does, these are proposed and made only when the person
applies them, through the same tag services the Tags panel uses. The suite is faked as in
test_assistant.py.
"""

import json

from sqlmodel import select

from app.models.asset import Asset
from app.models.tag import AssetTag, Tag, TagCategory
from tests.test_assistant import new_session, reply, run
from tests.test_assistant import fake_suite_fixture as fake_suite  # noqa: F401 - the fixture
from tests.test_tags import TEST_USER, add_tags, make_asset


def tag(session, name) -> Tag:
    return session.exec(select(Tag).where(Tag.name == name)).one()


def category(session, user_id=TEST_USER, **fields) -> TagCategory:
    row = TagCategory(user_id=user_id, **fields)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def answers(fake_suite, call=1) -> list:
    """What the tools said to the model, in the chat after `call`."""
    return [r for m in fake_suite.chats[call]["messages"] if m["role"] == "tool"
            for r in m["results"]]


def apply(library, turn, **body):
    res = library.post(f"/api/assistant/turns/{turn['id']}/apply", json=body or None)
    assert res.status_code == 200, res.text
    return res.json()["data"]


def found(library, word) -> list:
    return [row["asset"]["id"] for row in library.get("/api/search", params={"q": word}).json()["data"]]


# ─── grouping ────────────────────────────────────────────────────────────────


def test_tags_are_grouped_into_new_nested_categories_only_on_apply(library, session, fake_suite):
    asset = make_asset(session)
    add_tags(library, asset.id, "Giordano", "Schwab", "NATO")
    people = category(session, name="People")
    fake_suite.script += [
        reply("", ("propose_tag_groups", {
            "categories": [{"name": "Scientists", "parent": people.id},
                           {"name": "Organisations"}],
            "file": [{"tag_ids": [tag(session, "Giordano").id, tag(session, "Schwab").id],
                      "category": "Scientists"},
                     {"tag_ids": [tag(session, "NATO").id], "category": "organisations"}],
        })),
        reply("Grouped."),
    ]
    done = run(library, new_session(library), "Sort my tags into groups")

    ops = done["proposal"]["tag_ops"]
    assert [op["kind"] for op in ops] == ["create_category", "create_category", "file_tags",
                                         "file_tags"]
    assert ops[0]["parent_label"] == "People"
    assert ops[2]["category_label"] == "People > Scientists"
    assert sorted(t["name"] for t in ops[2]["tags"]) == ["Giordano", "Schwab"]
    assert ops[2]["asset_count"] == 1 and ops[2]["sample_assets"] == [asset.name]
    # Nothing exists yet.
    assert len(session.exec(select(TagCategory)).all()) == 1
    assert tag(session, "NATO").category_id is None

    results = apply(library, done)
    assert all(r["skipped"] == [] for r in results), results
    session.expire_all()
    scientists = session.exec(select(TagCategory).where(TagCategory.name == "Scientists")).one()
    organisations = session.exec(select(TagCategory).where(TagCategory.name == "Organisations")).one()
    assert scientists.parent_category_id == people.id and organisations.parent_category_id is None
    assert tag(session, "Giordano").category_id == scientists.id
    assert tag(session, "NATO").category_id == organisations.id
    # Filtering by the parent finds what was filed below it.
    listed = library.get("/api/assets", params={"category_id": people.id}).json()
    assert [a["id"] for a in listed["data"]] == [asset.id]


def test_a_category_that_exists_is_reused_not_twinned(library, session, fake_suite):
    asset = make_asset(session)
    add_tags(library, asset.id, "Giordano")
    people = category(session, name="People")
    fake_suite.script += [
        reply("", ("propose_tag_groups", {
            "categories": [{"name": "people"}],
            "file": [{"tag_ids": [tag(session, "Giordano").id], "category": "people"}],
        })),
        reply("Done."),
    ]
    done = run(library, new_session(library))
    assert [op["kind"] for op in done["proposal"]["tag_ops"]] == ["file_tags"]
    assert "exists already" in answers(fake_suite)[0]["content"]
    apply(library, done)
    session.expire_all()
    assert tag(session, "Giordano").category_id == people.id
    assert len(session.exec(select(TagCategory)).all()) == 1


def test_a_category_cannot_be_moved_inside_its_own_subcategory(library, session, fake_suite):
    people = category(session, name="People")
    scientists = category(session, name="Scientists", parent_category_id=people.id)
    fake_suite.script += [
        reply("", ("propose_tag_groups", {
            "moves": [{"category_id": people.id, "parent": scientists.id}]}),
            ("propose_tag_groups", {
                "categories": [{"name": "Inner", "parent": people.id}],
                "moves": [{"category_id": people.id, "parent": "Inner"}]})),
        reply("Couldn't."),
    ]
    done = run(library, new_session(library))
    first, second = answers(fake_suite)
    assert first["is_error"] and "inside itself" in first["content"]
    # A loop through a category proposed in the same turn is caught as well.
    assert "inside itself" in second["content"]
    assert [op["kind"] for op in done["proposal"]["tag_ops"]] == ["create_category"]


def test_an_existing_category_may_be_named_by_its_path(library, session, fake_suite):
    asset = make_asset(session)
    add_tags(library, asset.id, "Giordano", "misc")
    people = category(session, name="People")
    scientists = category(session, name="Scientists", parent_category_id=people.id)
    category(session, name="Other", parent_category_id=people.id)
    top_other = category(session, name="Other")
    fake_suite.script += [
        reply("", ("propose_tag_groups", {"file": [
            {"tag_ids": [tag(session, "Giordano").id], "category": "scientists"},
            # Two "Other"s, but only one whose full path is "Other".
            {"tag_ids": [tag(session, "misc").id], "category": "Other"}]})),
        reply("Done."),
    ]
    done = run(library, new_session(library))
    assert [op["category_id"] for op in done["proposal"]["tag_ops"]] == [scientists.id,
                                                                         top_other.id]


def test_an_ambiguous_category_name_is_refused(library, session, fake_suite):
    asset = make_asset(session)
    add_tags(library, asset.id, "Giordano")
    for parent in ("People", "Places"):
        row = category(session, name=parent)
        category(session, name="Other", parent_category_id=row.id)
    fake_suite.script += [
        reply("", ("propose_tag_groups", {"file": [{"tag_ids": [tag(session, "Giordano").id],
                                                    "category": "other"}]})),
        reply("Which one?"),
    ]
    run(library, new_session(library))
    (said,) = answers(fake_suite)
    assert said["is_error"] and "not a category id" in said["content"]


def test_a_tag_filed_twice_goes_to_the_latest_group(library, session, fake_suite):
    asset = make_asset(session)
    add_tags(library, asset.id, "Giordano")
    one, two = category(session, name="One"), category(session, name="Two")
    gid = tag(session, "Giordano").id
    fake_suite.script += [
        reply("", ("propose_tag_groups", {"file": [{"tag_ids": [gid], "category": one.id}]}),
              ("propose_tag_groups", {"file": [{"tag_ids": [gid], "category": two.id}]})),
        reply("Done."),
    ]
    done = run(library, new_session(library))
    (op,) = done["proposal"]["tag_ops"]
    assert op["category_id"] == two.id


# ─── renaming and merging ────────────────────────────────────────────────────


def test_a_rename_applies_to_every_asset_and_moves_the_searchable_word(library, session, fake_suite):
    a, b = make_asset(session, name="One"), make_asset(session, name="Two")
    add_tags(library, a.id, "intervew")
    add_tags(library, b.id, "intervew")
    fake_suite.script += [
        reply("", ("propose_tag_rename", {
            "renames": [{"tag_id": tag(session, "intervew").id, "new_name": "interview"}]})),
        reply("Fixed the spelling."),
    ]
    done = run(library, new_session(library))
    (op,) = done["proposal"]["tag_ops"]
    assert (op["kind"], op["from"], op["to"], op["asset_count"]) == ("rename_tag", "intervew",
                                                                     "interview", 2)
    assert op["sample_assets"] == ["One", "Two"]
    assert found(library, "interview") == []

    apply(library, done)
    assert sorted(found(library, "interview")) == sorted([a.id, b.id])
    assert found(library, "intervew") == []


def test_a_rename_onto_an_existing_name_is_a_merge(library, session, fake_suite):
    a, b, both = (make_asset(session, name=n) for n in ("One", "Two", "Both"))
    add_tags(library, a.id, "nato")
    add_tags(library, b.id, "N.A.T.O.")
    add_tags(library, both.id, "nato", "N.A.T.O.")
    people = category(session, name="Organisations")
    small = tag(session, "N.A.T.O.")
    small.category_id = people.id
    session.add(small)
    session.commit()
    fake_suite.script += [
        reply("", ("propose_tag_rename", {
            "renames": [{"tag_id": small.id, "new_name": "NATO"}]})),
        reply("Merged."),
    ]
    done = run(library, new_session(library))
    (op,) = done["proposal"]["tag_ops"]
    assert op["kind"] == "merge_tags" and op["target_id"] == tag(session, "nato").id
    assert op["asset_count"] == 2  # the assets carrying the source

    (result,) = apply(library, done)
    assert result["skipped"] == [] and result["op_ref"] == op["op_ref"]
    session.expire_all()
    target = tag(session, "nato")
    assert session.exec(select(Tag).where(Tag.name == "N.A.T.O.")).first() is None
    carriers = session.exec(select(AssetTag.asset_id).where(AssetTag.tag_id == target.id)).all()
    # Each asset once, including the one that already carried both.
    assert sorted(carriers) == sorted([a.id, b.id, both.id])
    # The target had no category, so it takes the source's.
    assert target.category_id == people.id


def test_a_merge_into_a_new_name_makes_the_tag(library, session, fake_suite):
    a = make_asset(session)
    add_tags(library, a.id, "interview", "Interviews")
    ids = [tag(session, "interview").id, tag(session, "Interviews").id]
    fake_suite.script += [
        reply("", ("propose_tag_rename", {
            "merges": [{"source_tag_ids": ids, "target_name": "Interview (video)"}]})),
        reply("Merged."),
    ]
    done = run(library, new_session(library))
    apply(library, done)
    session.expire_all()
    assert [t.name for t in session.exec(select(Tag)).all()] == ["Interview (video)"]
    assert found(library, "video") == [a.id]


def test_one_tag_is_not_renamed_and_deleted_in_one_proposal(library, session, fake_suite):
    a = make_asset(session)
    add_tags(library, a.id, "tmp")
    tid = tag(session, "tmp").id
    fake_suite.script += [
        reply("", ("propose_tag_rename", {"renames": [{"tag_id": tid, "new_name": "temp"}]}),
              ("propose_tag_delete", {"tag_ids": [tid]})),
        reply("Done."),
    ]
    done = run(library, new_session(library))
    assert answers(fake_suite)[1]["is_error"]
    assert [op["kind"] for op in done["proposal"]["tag_ops"]] == ["rename_tag"]


# ─── deleting ────────────────────────────────────────────────────────────────


def test_unused_tags_are_found_and_deleted_only_on_apply(library, session, fake_suite):
    a = make_asset(session)
    add_tags(library, a.id, "keep")
    library.post("/api/tags", json={"name": "unused"})
    fake_suite.script += [
        reply("", ("list_tags", {"max_uses": 0})),
        reply("", ("propose_tag_delete", {"tag_ids": [tag(session, "unused").id]})),
        reply("Removed one unused tag."),
    ]
    done = run(library, new_session(library))
    listing = answers(fake_suite)[0]["content"]
    assert "unused (0)" in listing and "keep" not in listing
    (op,) = done["proposal"]["tag_ops"]
    assert op["asset_count"] == 0
    assert session.exec(select(Tag).where(Tag.name == "unused")).first() is not None

    apply(library, done)
    session.expire_all()
    assert [t.name for t in session.exec(select(Tag)).all()] == ["keep"]


# ─── choosing, staleness, ownership ──────────────────────────────────────────


def test_apply_takes_the_chosen_ops_and_skips_stale_ones_with_a_sentence(
        library, session, fake_suite):
    a = make_asset(session)
    add_tags(library, a.id, "one", "two", "three")
    fake_suite.script += [
        reply("", ("propose_tag_groups", {"categories": [{"name": "Numbers"}],
                                          "file": [{"tag_ids": [tag(session, "one").id],
                                                    "category": "Numbers"}]}),
              ("propose_tag_delete", {"tag_ids": [tag(session, "two").id,
                                                  tag(session, "three").id]})),
        reply("Done."),
    ]
    done = run(library, new_session(library))
    create, file, del_two, del_three = done["proposal"]["tag_ops"]
    # "three" goes away before Apply; the category is not chosen.
    library.delete(f"/api/tags/{tag(session, 'three').id}")

    results = apply(library, done, tag_op_refs=[file["op_ref"], del_two["op_ref"],
                                                del_three["op_ref"]])
    by_ref = {r["op_ref"]: r for r in results}
    assert create["op_ref"] not in by_ref
    assert "not made" in by_ref[file["op_ref"]]["skipped"][0]
    assert by_ref[del_two["op_ref"]]["applied"] == ["deleted"]
    assert by_ref[del_three["op_ref"]]["skipped"] == ["The tag no longer exists."]
    session.expire_all()
    assert session.exec(select(TagCategory)).all() == []
    assert tag(session, "one").category_id is None


def test_choosing_assets_alone_applies_no_tag_changes(library, session, fake_suite):
    """A client that names the assets it wants has never heard of tag changes, and must not
    get them as a side effect."""
    a = make_asset(session)
    add_tags(library, a.id, "old")
    fake_suite.script += [
        reply("", ("propose_changes", {"asset_id": a.id, "name": "Renamed"}),
              ("propose_tag_delete", {"tag_ids": [tag(session, "old").id]})),
        reply("Done."),
    ]
    done = run(library, new_session(library))
    apply(library, done, asset_ids=[a.id])
    session.expire_all()
    assert session.get(Asset, a.id).name == "Renamed"
    assert tag(session, "old") is not None


def test_someone_elses_tags_and_categories_are_reported_missing(library, session, fake_suite):
    theirs = Tag(user_id="someone-else", name="private")
    session.add(theirs)
    session.commit()
    their_group = category(session, user_id="someone-else", name="Theirs")
    fake_suite.script += [
        reply("", ("propose_tag_delete", {"tag_ids": [theirs.id]}),
              ("propose_tag_rename", {"renames": [{"tag_id": theirs.id, "new_name": "mine"}]}),
              ("propose_tag_groups", {"moves": [{"category_id": their_group.id, "new_name": "x"}]})),
        reply("Couldn't."),
    ]
    done = run(library, new_session(library))
    assert all(r["is_error"] for r in answers(fake_suite))
    assert done["proposal"] is None
    session.refresh(theirs)
    assert theirs.name == "private"


# ─── looking ─────────────────────────────────────────────────────────────────


def test_similar_tags_are_found_in_groups(library, session, fake_suite):
    a = make_asset(session)
    add_tags(library, a.id, "interview", "Interviews", "intervew", "NATO", "nato.", "river")
    fake_suite.script += [reply("", ("find_similar_tags", {})), reply("Found two groups.")]
    run(library, new_session(library))
    text = answers(fake_suite)[0]["content"]
    lines = [line for line in text.splitlines() if line.startswith("- ")]
    assert len(lines) == 2
    assert all(n in " ".join(lines) for n in ("interview", "Interviews", "intervew", "NATO", "nato."))
    assert "river" not in text


def test_a_large_vocabulary_is_listed_in_pages_and_can_all_be_filed(library, session, fake_suite):
    a = make_asset(session)
    for start in range(0, 250, 50):
        add_tags(library, a.id, *(f"tag {n:03d}" for n in range(start, start + 50)))
    filed = category(session, name="Old")
    old = tag(session, "tag 000")
    old.category_id = filed.id
    session.add(old)
    session.commit()
    ids = [t.id for t in session.exec(select(Tag)).all() if t.name != "tag 000"]
    fake_suite.script += [
        reply("", ("list_tags", {}), ("list_tags", {"offset": 200}),
              ("list_tags", {"uncategorised_only": True})),
        reply("", ("propose_tag_groups", {
            "categories": [{"name": "Numbers"}] + [
                {"name": f"Batch {n}", "parent": "Numbers"} for n in range(5)],
            "file": [{"tag_ids": ids[n * 50:(n + 1) * 50], "category": f"Batch {n}"}
                     for n in range(5)],
        })),
        reply("All filed."),
    ]
    done = run(library, new_session(library))
    first, second, unfiled = (r["content"] for r in answers(fake_suite))
    assert "250 tag(s), 249 not in any category; 1 category." in first
    assert "Tags 1-200 of 250" in first and "[more: call again with offset=200]" in first
    assert f"{filed.id} | Old" in first
    assert "Tags 201-250 of 250" in second and "[more" not in second
    assert "of 249" in unfiled and "| Old" not in unfiled.split("Tags")[1]

    assert len(done["proposal"]["tag_ops"]) == 11
    apply(library, done)
    session.expire_all()
    assert [t.name for t in session.exec(select(Tag).where(Tag.category_id.is_(None))).all()] == []


def test_a_turn_from_before_tag_changes_existed_still_applies(library, session, fake_suite):
    a = make_asset(session)
    fake_suite.script += [reply("", ("propose_changes", {"asset_id": a.id, "name": "New"})),
                          reply("Done.")]
    done = run(library, new_session(library))
    from app.models.assistant import AssistantTurn

    row = session.get(AssistantTurn, done["id"])
    proposal = json.loads(row.proposal)
    proposal.pop("tag_ops")
    row.proposal = json.dumps(proposal)
    session.add(row)
    session.commit()
    (result,) = apply(library, done)
    assert result["applied"] == ["name"]
