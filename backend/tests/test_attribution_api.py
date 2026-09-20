"""Attribution over HTTP: editing, inheritance, filtering and search (M10)."""

from pathlib import Path

from sqlmodel import select

from app.models.asset import Asset

FIXTURES = Path(__file__).parent / "fixtures"


def _upload(client, *names: str):
    files = [
        ("files", (name, (FIXTURES / name).read_bytes(), "application/octet-stream"))
        for name in names
    ]
    return client.post("/api/assets", files=files)


def _upload_one(client, name: str = "sample_video.mp4") -> dict:
    return _upload(client, name).json()["created"][0]


# ─── editing ─────────────────────────────────────────────────────────────────


def test_attribution_is_editable_by_hand(library):
    asset = _upload_one(library)

    response = library.patch(
        f"/api/assets/{asset['id']}",
        json={
            "creator": "Jane Doe",
            "publisher": "BBC",
            "source_title": "Panorama",
            "published_date": "2019-03",
            "license": "CC BY 4.0",
            "source_url": "https://example.org/panorama",
        },
    )
    assert response.status_code == 200

    updated = response.json()["data"]
    assert updated["publisher"] == "BBC"
    assert updated["credit"] == "Jane Doe — Panorama — BBC — 2019-03 — CC BY 4.0"


def test_a_typed_credit_line_overrides_the_composition(library):
    asset = _upload_one(library)
    response = library.patch(
        f"/api/assets/{asset['id']}",
        json={"publisher": "BBC", "credit_line": "Courtesy of the BBC"},
    )
    assert response.json()["data"]["credit"] == "Courtesy of the BBC"


def test_correcting_a_field_recomposes_the_credit(library):
    """The reason the composition is not stored."""
    asset = _upload_one(library)
    library.patch(f"/api/assets/{asset['id']}", json={"publisher": "BBC Two"})
    assert library.get(f"/api/assets/{asset['id']}").json()["data"]["credit"] == "BBC Two"

    library.patch(f"/api/assets/{asset['id']}", json={"publisher": "BBC Four"})
    assert library.get(f"/api/assets/{asset['id']}").json()["data"]["credit"] == "BBC Four"


def test_a_partial_published_date_is_accepted(library):
    asset = _upload_one(library)
    for value in ("1994", "2019-03", "2019-03-15"):
        response = library.patch(f"/api/assets/{asset['id']}", json={"published_date": value})
        assert response.status_code == 200, value
        assert response.json()["data"]["published_date"] == value


def test_a_malformed_published_date_is_refused(library):
    """The string column is what allows a partial date; the validator is what stops it
    becoming free text, which would break the lexicographic range filters."""
    asset = _upload_one(library)
    for value in ("summer 1994", "15/03/2019", "2019-13", "2019-3"):
        response = library.patch(f"/api/assets/{asset['id']}", json={"published_date": value})
        assert response.status_code == 422, value


# ─── inheritance ─────────────────────────────────────────────────────────────


def _clip_of(library, parent_id: str) -> dict:
    response = library.post(
        f"/api/assets/{parent_id}/clips", json={"in_point": 0.2, "out_point": 0.8}
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_a_clip_inherits_its_parents_attribution(library):
    parent = _upload_one(library)
    library.patch(
        f"/api/assets/{parent['id']}",
        json={"creator": "Jane Doe", "publisher": "BBC", "source_title": "Panorama"},
    )

    clip = _clip_of(library, parent["id"])
    assert clip["publisher"] == "BBC"
    assert clip["credit"] == "Jane Doe — Panorama — BBC"
    assert set(clip["attribution_inherited"]) == {"creator", "publisher", "source_title"}


def test_a_clip_can_override_one_field_and_keep_the_rest(library):
    parent = _upload_one(library)
    library.patch(
        f"/api/assets/{parent['id']}",
        json={"creator": "Jane Doe", "publisher": "BBC"},
    )
    clip = _clip_of(library, parent["id"])

    updated = library.patch(
        f"/api/assets/{clip['id']}", json={"creator": "A different interviewee"}
    ).json()["data"]

    assert updated["creator"] == "A different interviewee"
    assert updated["publisher"] == "BBC"
    assert updated["attribution_inherited"] == ["publisher"]


def test_correcting_the_parent_reaches_every_clip_of_it(library):
    """The property that made copy-on-create the wrong design."""
    parent = _upload_one(library)
    library.patch(f"/api/assets/{parent['id']}", json={"publisher": "BBC Two"})
    clip = _clip_of(library, parent["id"])
    assert clip["publisher"] == "BBC Two"

    library.patch(f"/api/assets/{parent['id']}", json={"publisher": "BBC Four"})

    refreshed = library.get(f"/api/assets/{clip['id']}").json()["data"]
    assert refreshed["publisher"] == "BBC Four"


def test_an_unattributed_parent_leaves_the_clip_blank(library):
    parent = _upload_one(library)
    clip = _clip_of(library, parent["id"])
    assert clip["credit"] == ""
    assert clip["attribution_inherited"] == []


# ─── filters ─────────────────────────────────────────────────────────────────


def test_filtering_by_publisher(library):
    first = _upload_one(library, "sample_video.mp4")
    second = _upload_one(library, "sample_image.jpg")
    library.patch(f"/api/assets/{first['id']}", json={"publisher": "BBC"})
    library.patch(f"/api/assets/{second['id']}", json={"publisher": "Channel 4"})

    body = library.get("/api/assets", params={"publisher": "BBC"}).json()
    assert [row["id"] for row in body["data"]] == [first["id"]]
    assert body["total"] == 1


def test_filtering_by_publisher_also_returns_the_clips_that_inherit_it(library):
    """A filter that only looked at the row would answer "everything from the BBC" with
    the documentary and none of the clips cut from it."""
    parent = _upload_one(library)
    library.patch(f"/api/assets/{parent['id']}", json={"publisher": "BBC"})
    clip = _clip_of(library, parent["id"])

    body = library.get("/api/assets", params={"publisher": "BBC"}).json()
    assert {row["id"] for row in body["data"]} == {parent["id"], clip["id"]}


def test_a_clip_that_overrides_the_publisher_drops_out_of_the_parents_filter(library):
    parent = _upload_one(library)
    library.patch(f"/api/assets/{parent['id']}", json={"publisher": "BBC"})
    clip = _clip_of(library, parent["id"])
    library.patch(f"/api/assets/{clip['id']}", json={"publisher": "Channel 4"})

    body = library.get("/api/assets", params={"publisher": "BBC"}).json()
    assert [row["id"] for row in body["data"]] == [parent["id"]]


def test_filtering_by_creator_and_source_title(library):
    asset = _upload_one(library)
    library.patch(
        f"/api/assets/{asset['id']}", json={"creator": "Jane Doe", "source_title": "Panorama"}
    )

    assert library.get("/api/assets", params={"creator": "jane"}).json()["total"] == 1
    assert library.get("/api/assets", params={"source_title": "panorama"}).json()["total"] == 1
    assert library.get("/api/assets", params={"creator": "nobody"}).json()["total"] == 0


def test_published_date_ranges_compare_chronologically(library):
    """Lexicographic comparison is chronological because the format is guaranteed."""
    older = _upload_one(library, "sample_video.mp4")
    newer = _upload_one(library, "sample_image.jpg")
    library.patch(f"/api/assets/{older['id']}", json={"published_date": "2018-12-31"})
    library.patch(f"/api/assets/{newer['id']}", json={"published_date": "2019-03-01"})

    after = library.get("/api/assets", params={"published_after": "2019"}).json()
    assert [row["id"] for row in after["data"]] == [newer["id"]]

    before = library.get("/api/assets", params={"published_before": "2019"}).json()
    assert [row["id"] for row in before["data"]] == [older["id"]]


def test_unattributed_returns_exactly_what_is_still_missing_a_source(library):
    attributed = _upload_one(library, "sample_video.mp4")
    blank = _upload_one(library, "sample_image.jpg")
    library.patch(f"/api/assets/{attributed['id']}", json={"publisher": "BBC"})

    body = library.get("/api/assets", params={"unattributed": "true"}).json()
    assert [row["id"] for row in body["data"]] == [blank["id"]]

    inverse = library.get("/api/assets", params={"unattributed": "false"}).json()
    assert [row["id"] for row in inverse["data"]] == [attributed["id"]]


def test_a_clip_that_inherits_is_not_unattributed(library):
    """It is not missing a source — it shows its parent's. Counting it would put rows in
    the backlog that there is nothing to do about."""
    parent = _upload_one(library)
    library.patch(f"/api/assets/{parent['id']}", json={"publisher": "BBC"})
    clip = _clip_of(library, parent["id"])

    ids = {row["id"] for row in library.get("/api/assets", params={"unattributed": "true"}).json()["data"]}
    assert clip["id"] not in ids
    assert parent["id"] not in ids


def test_a_whitespace_only_value_still_counts_as_unattributed(library):
    asset = _upload_one(library)
    library.patch(f"/api/assets/{asset['id']}", json={"publisher": "   "})

    body = library.get("/api/assets", params={"unattributed": "true"}).json()
    assert [row["id"] for row in body["data"]] == [asset["id"]]


# ─── search ──────────────────────────────────────────────────────────────────


def test_an_asset_is_findable_by_its_publisher(library):
    asset = _upload_one(library)
    library.patch(
        f"/api/assets/{asset['id']}", json={"publisher": "BBC", "source_title": "Panorama"}
    )

    for term in ("BBC", "Panorama"):
        body = library.get("/api/search", params={"q": term}).json()
        assert [hit["asset"]["id"] for hit in body["data"]] == [asset["id"]], term


def test_a_clip_is_findable_by_the_publisher_it_inherited(library):
    parent = _upload_one(library)
    library.patch(f"/api/assets/{parent['id']}", json={"publisher": "BBC"})
    clip = _clip_of(library, parent["id"])

    found = {hit["asset"]["id"] for hit in library.get("/api/search", params={"q": "BBC"}).json()["data"]}
    assert clip["id"] in found


def test_correcting_a_parent_reindexes_its_clips(library, session):
    """The keyword index stores a snapshot, so it is the one place inheritance cannot be
    resolved on read — without the child re-index, a clip stays searchable under the old
    publisher and invisible under the new one."""
    parent = _upload_one(library)
    library.patch(f"/api/assets/{parent['id']}", json={"publisher": "BBC"})
    clip = _clip_of(library, parent["id"])

    library.patch(f"/api/assets/{parent['id']}", json={"publisher": "Channel 4"})

    found = {hit["asset"]["id"] for hit in library.get("/api/search", params={"q": "Channel"}).json()["data"]}
    assert clip["id"] in found

    stale = {hit["asset"]["id"] for hit in library.get("/api/search", params={"q": "BBC"}).json()["data"]}
    assert clip["id"] not in stale


def test_attribution_does_not_leak_between_users(library, session):
    """Every filter above narrows within one user's library; this is the one that would
    be a disclosure rather than a wrong answer."""
    asset = _upload_one(library)
    library.patch(f"/api/assets/{asset['id']}", json={"publisher": "BBC"})

    stored = session.exec(select(Asset)).first()
    stored.user_id = "somebody-else"
    session.add(stored)
    session.commit()

    assert library.get("/api/assets", params={"publisher": "BBC"}).json()["total"] == 0
    assert library.get("/api/search", params={"q": "BBC"}).json()["data"] == []


# ─── the library-wide re-harvest ─────────────────────────────────────────────


def _run_job(job_id, session, monkeypatch):
    """Run a queued job against the test database.

    The queue builds its own session from its own engine, so it has to be pointed at
    this test's — the same helper `test_embedding_backfill.py` uses for library jobs.
    """
    from app.jobs import enrichment as enrichment_jobs

    monkeypatch.setattr(enrichment_jobs.queue(), "engine", session.get_bind())
    enrichment_jobs._run_job(job_id)
    session.expire_all()


def test_harvest_queues_one_library_job(library, session):
    from app.models.job import KIND_HARVEST_ATTRIBUTION, EnrichmentJob

    _upload(library, "sample_image.jpg", "sample_video.mp4")

    response = library.post("/api/assets/harvest-attribution")

    assert response.status_code == 202
    body = response.json()["data"]
    assert body["action"] == KIND_HARVEST_ATTRIBUTION
    assert body["asset_id"] is None
    assert len(session.exec(select(EnrichmentJob)).all()) == 1


def test_a_second_harvest_while_one_runs_is_a_409(library, session):
    library.post("/api/assets/harvest-attribution")
    assert library.post("/api/assets/harvest-attribution").status_code == 409


def test_the_harvest_attributes_files_uploaded_before_it_existed(library, session, monkeypatch):
    """The case this endpoint exists for: the metadata was on disk the whole time and
    nothing had ever looked."""
    asset = _upload_one(library, "attributed_image.jpg")

    # Wind the row back to how it would look had it been ingested before M10.
    stored = session.get(Asset, asset["id"])
    stored.creator = None
    stored.license = None
    stored.published_date = None
    stored.field_provenance = "{}"
    session.add(stored)
    session.commit()

    job = library.post("/api/assets/harvest-attribution").json()["data"]
    _run_job(job["id"], session, monkeypatch)

    refreshed = library.get(f"/api/assets/{asset['id']}").json()["data"]
    assert refreshed["creator"] == "Jane Doe"
    assert refreshed["license"] == "(C) 2019 BBC"


def test_the_harvest_never_overwrites_a_hand_typed_value(library, session, monkeypatch):
    """Which is what makes it safe to run twice, and safe to run unasked."""
    asset = _upload_one(library, "attributed_image.jpg")
    library.patch(f"/api/assets/{asset['id']}", json={"creator": "The actual photographer"})

    job = library.post("/api/assets/harvest-attribution").json()["data"]
    _run_job(job["id"], session, monkeypatch)

    refreshed = library.get(f"/api/assets/{asset['id']}").json()["data"]
    assert refreshed["creator"] == "The actual photographer"


def test_the_harvest_reports_what_it_did(library, session, monkeypatch):
    from app.models.job import EnrichmentJob

    _upload(library, "attributed_image.jpg", "sample_image.jpg")
    stored = session.exec(select(Asset).where(Asset.creator.is_not(None))).first()
    stored.creator = None
    stored.license = None
    stored.published_date = None
    session.add(stored)
    session.commit()

    job = library.post("/api/assets/harvest-attribution").json()["data"]
    _run_job(job["id"], session, monkeypatch)

    finished = session.get(EnrichmentJob, job["id"])
    assert finished.status == "done"
    assert "1 attributed of 2 scanned" in finished.detail


def test_the_harvest_skips_clips_which_own_no_bytes(library, session):
    """A clip inherits on read; there is no file under it to read metadata from."""
    from app.enrichment.harvest_attribution import run

    parent = _upload_one(library, "attributed_video.mp4")
    _clip_of(library, parent["id"])

    result = run(session, session.get(Asset, parent["id"]).user_id, lambda *a, **k: None)
    assert result.scanned == 1
