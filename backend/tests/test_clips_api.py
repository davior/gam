"""Clips and sub-video extraction, over HTTP and against real media."""

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import select

from app.enrichment import source
from app.enrichment.source import NoSourceMaterial
from app.ingest.probe import ProbeResult
from app.jobs import enrichment as enrichment_jobs
from app.models.asset import Asset
from app.models.job import EnrichmentJob, KIND_EXTRACT_SUBVIDEO
from app.models.tag import AssetTag
from app.search import fts
from app.services import assets as asset_service
from app.storage import thumb_key_for
from app.storage.base import StoredFile
from app.media_tools import ffmpeg_available

FIXTURES = Path(__file__).parent / "fixtures"

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def _upload_video(client):
    return client.post(
        "/api/assets",
        files=[("files", ("sample_video.mp4", (FIXTURES / "sample_video.mp4").read_bytes(), "video/mp4"))],
    ).json()["created"][0]


def _upload_audio(client):
    return client.post(
        "/api/assets",
        files=[("files", ("sample_audio.mp3", (FIXTURES / "sample_audio.mp3").read_bytes(), "audio/mpeg"))],
    ).json()["created"][0]


def _upload_image(client):
    return client.post(
        "/api/assets",
        files=[("files", ("sample_image.jpg", (FIXTURES / "sample_image.jpg").read_bytes(), "image/jpeg"))],
    ).json()["created"][0]


def _fake_stored(key: str = "user-under-test/fake-subvideo.mp4") -> StoredFile:
    return StoredFile(key=key, size_bytes=12345, sha256="0" * 64)


def _fake_probe(duration: float = 9.0) -> ProbeResult:
    return ProbeResult(duration_seconds=duration, width=320, height=240, codec="h264")


def _clip(client, parent_id: str, in_point: float = 0.0, out_point: float = 0.5, **fields):
    return client.post(
        f"/api/assets/{parent_id}/clips",
        json={"in_point": in_point, "out_point": out_point, **fields},
    ).json()["data"]


def _files(media_dir) -> set[str]:
    return {str(p.relative_to(media_dir)) for p in Path(media_dir).rglob("*") if p.is_file()}


def _give_a_poster(session, media_dir, asset_id: str) -> Path:
    """Put a poster on disk for this asset, so clips cut afterwards copy its key.

    By hand, so the shared-key path runs whether or not ffmpeg is on PATH — the same
    reason `test_clip_inherits_the_parents_thumbnail` sets one. Written at the key the
    upload's own poster uses, not a made-up one: a poster ffmpeg did generate is then
    overwritten rather than orphaned beside it, where a "nothing left on disk"
    assertion would trip over it.
    """
    row = session.get(Asset, asset_id)
    row.thumb_key = thumb_key_for(row.storage_key)
    path = Path(media_dir) / row.thumb_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8poster")
    session.add(row)
    session.commit()
    return path


# ─── creating a clip (synchronous, no ffmpeg needed) ──────────────────────────


def test_create_clip_is_a_window_not_a_copy(library, media_dir):
    parent = _upload_video(library)
    before = {p for p in Path(media_dir).rglob("*") if p.is_file()}

    response = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.2, "out_point": 0.6}
    )
    assert response.status_code == 201

    clip = response.json()["data"]
    assert clip["source"] == "clip"
    assert clip["parent_asset_id"] == parent["id"]
    assert clip["in_point"] == pytest.approx(0.2)
    assert clip["out_point"] == pytest.approx(0.6)
    assert clip["duration_seconds"] == pytest.approx(0.4)
    assert clip["asset_type"] == "video"

    # Zero storage, zero processing — no new file exists on disk.
    after = {p for p in Path(media_dir).rglob("*") if p.is_file()}
    assert after == before


def test_clip_default_name_mentions_the_range(library, session):
    parent = _upload_video(library)
    # The fixture is only ~2s long; set a longer duration directly so a >60s range is
    # legal, the same way test_create_clip_refuses_past_the_parents_duration does.
    parent_row = session.get(Asset, parent["id"])
    parent_row.duration_seconds = 120.0
    session.add(parent_row)
    session.commit()

    clip = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 61.0, "out_point": 70.0}
    ).json()["data"]
    assert "1:01" in clip["name"]
    assert "1:10" in clip["name"]


def test_clip_can_be_named_explicitly(library):
    parent = _upload_video(library)
    clip = library.post(
        f"/api/assets/{parent['id']}/clips",
        json={"in_point": 0.0, "out_point": 0.5, "name": "The nano weapons moment"},
    ).json()["data"]
    assert clip["name"] == "The nano weapons moment"


def test_clip_inherits_the_parents_thumbnail(library, session):
    parent = _upload_video(library)
    parent_row = session.get(Asset, parent["id"])
    # This sandbox has no ffmpeg, so the upload itself never got a poster — set one by
    # hand so the inheritance path (not the probing path) is what is under test.
    parent_row.thumb_key = f"{parent_row.user_id}/fake-poster.thumb.jpg"
    session.add(parent_row)
    session.commit()

    clip = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    ).json()["data"]
    assert clip["thumb_url"] is not None
    assert parent_row.thumb_key in clip["thumb_url"]


def test_clips_file_url_resolves_to_the_parents_key(library, session):
    parent = _upload_video(library)
    parent_row = session.get(Asset, parent["id"])

    clip = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    ).json()["data"]
    assert clip["file_url"] is not None
    assert parent_row.storage_key in clip["file_url"]

    # Same through GET, not just the create response.
    fetched = library.get(f"/api/assets/{clip['id']}").json()["data"]
    assert parent_row.storage_key in fetched["file_url"]


def test_clips_missing_follows_the_parent(library, session, media_dir):
    parent = _upload_video(library)
    clip = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    ).json()["data"]

    for path in Path(media_dir).rglob("*"):
        if path.is_file() and ".thumb" not in path.name:
            path.unlink()

    fetched = library.get(f"/api/assets/{clip['id']}").json()["data"]
    assert fetched["missing"] is True


def test_clip_appears_in_the_library_listing(library):
    parent = _upload_video(library)
    clip = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    ).json()["data"]

    body = library.get("/api/assets").json()
    ids = {a["id"] for a in body["data"]}
    assert clip["id"] in ids


def test_create_clip_refuses_a_non_video_asset(library):
    image = _upload_image(library)
    response = library.post(
        f"/api/assets/{image['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_clip_range"


def test_create_clip_refuses_a_backwards_range(library):
    parent = _upload_video(library)
    response = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 5.0, "out_point": 1.0}
    )
    assert response.status_code == 400


def test_create_clip_refuses_past_the_parents_duration(library, session):
    parent = _upload_video(library)
    # ffprobe is unavailable in this sandbox, so a real upload never gets a duration —
    # set one directly so the bounds check (not the probing path) is under test.
    parent_row = session.get(Asset, parent["id"])
    parent_row.duration_seconds = 2.0
    session.add(parent_row)
    session.commit()

    response = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 5.0}
    )
    assert response.status_code == 400


def test_cannot_clip_a_clip(library):
    parent = _upload_video(library)
    clip = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    ).json()["data"]

    response = library.post(
        f"/api/assets/{clip['id']}/clips", json={"in_point": 0.0, "out_point": 0.1}
    )
    assert response.status_code == 400


def test_create_clip_of_someone_elses_asset_is_a_404(library, session):
    other = Asset(user_id="somebody-else", name="Theirs", asset_type="video", source="local_upload")
    session.add(other)
    session.commit()

    response = library.post(
        f"/api/assets/{other.id}/clips", json={"in_point": 0.0, "out_point": 0.5}
    )
    assert response.status_code == 404


# ─── listing clips ─────────────────────────────────────────────────────────────


def test_list_clips_returns_a_parents_children(library):
    parent = _upload_video(library)
    library.post(f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.2})
    library.post(f"/api/assets/{parent['id']}/clips", json={"in_point": 0.3, "out_point": 0.5})

    body = library.get(f"/api/assets/{parent['id']}/clips").json()
    assert body["total"] == 2
    assert {c["parent_asset_id"] for c in body["data"]} == {parent["id"]}


def test_list_clips_includes_a_promoted_one(library, session):
    """`GET .../clips` is the full history, not just the still-live ones — the delete
    guard's promote UI is the caller that narrows it further, to `source == "clip"`."""
    parent = _upload_video(library)
    clip = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    ).json()["data"]

    clip_row = session.get(Asset, clip["id"])
    asset_service.promote_clip(
        session, clip_row, stored=_fake_stored(), thumb_key=None, probe_result=_fake_probe()
    )

    body = library.get(f"/api/assets/{parent['id']}/clips").json()
    assert body["total"] == 1
    assert body["data"][0]["source"] == "sub_video"


def test_list_clips_of_someone_elses_asset_is_a_404(library, session):
    other = Asset(user_id="somebody-else", name="Theirs", asset_type="video", source="local_upload")
    session.add(other)
    session.commit()
    assert library.get(f"/api/assets/{other.id}/clips").status_code == 404


# ─── starting a sub-video extraction (queues a job) ───────────────────────────


def test_subvideo_queues_a_job(library):
    asset = _upload_video(library)
    response = library.post(
        f"/api/assets/{asset['id']}/subvideo", json={"in_point": 0.0, "out_point": 0.5}
    )
    assert response.status_code == 202

    job = response.json()["data"]
    assert job["action"] == "extract_subvideo"
    assert job["status"] == "queued"
    assert job["asset_id"] == asset["id"]
    assert job["result_asset_id"] is None


def test_subvideo_refuses_a_non_video_asset(library):
    image = _upload_image(library)
    response = library.post(
        f"/api/assets/{image['id']}/subvideo", json={"in_point": 0.0, "out_point": 0.5}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "not_clippable"


def test_subvideo_refuses_a_clip_as_its_own_source(library):
    """A clip owns no bytes — extracting *from* one is refused the same as clipping one
    (`create_clip`'s own rule); `promote` is the path that turns a clip into a file."""
    parent = _upload_video(library)
    clip = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    ).json()["data"]

    response = library.post(
        f"/api/assets/{clip['id']}/subvideo", json={"in_point": 0.0, "out_point": 0.1}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "not_clippable"


def test_subvideo_refuses_a_backwards_range(library):
    asset = _upload_video(library)
    response = library.post(
        f"/api/assets/{asset['id']}/subvideo", json={"in_point": 5.0, "out_point": 1.0}
    )
    assert response.status_code == 400


def test_subvideo_refuses_a_second_concurrent_run(library):
    asset = _upload_video(library)
    library.post(f"/api/assets/{asset['id']}/subvideo", json={"in_point": 0.0, "out_point": 0.5})

    response = library.post(
        f"/api/assets/{asset['id']}/subvideo", json={"in_point": 0.0, "out_point": 0.4}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "already_running"


def test_subvideo_of_someone_elses_asset_is_a_404(library, session):
    other = Asset(user_id="somebody-else", name="Theirs", asset_type="video", source="local_upload")
    session.add(other)
    session.commit()
    response = library.post(
        f"/api/assets/{other.id}/subvideo", json={"in_point": 0.0, "out_point": 0.5}
    )
    assert response.status_code == 404


# ─── promoting a clip (queues a job) ───────────────────────────────────────────


def test_promote_queues_a_job(library):
    parent = _upload_video(library)
    clip = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    ).json()["data"]

    response = library.post(f"/api/assets/{clip['id']}/promote")
    assert response.status_code == 202
    job = response.json()["data"]
    assert job["action"] == "extract_subvideo"
    assert job["asset_id"] == clip["id"]


def test_promote_refuses_a_non_clip(library):
    asset = _upload_video(library)
    response = library.post(f"/api/assets/{asset['id']}/promote")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "not_a_clip"


def test_promote_refuses_a_second_concurrent_run(library):
    parent = _upload_video(library)
    clip = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    ).json()["data"]
    library.post(f"/api/assets/{clip['id']}/promote")

    response = library.post(f"/api/assets/{clip['id']}/promote")
    assert response.status_code == 409


def test_promote_of_someone_elses_clip_is_a_404(library, session):
    other = Asset(
        user_id="somebody-else",
        name="Theirs",
        asset_type="video",
        source="clip",
        parent_asset_id=None,
    )
    session.add(other)
    session.commit()
    assert library.post(f"/api/assets/{other.id}/promote").status_code == 404


# ─── the delete guard ──────────────────────────────────────────────────────────


def test_delete_is_blocked_by_a_live_clip(library):
    parent = _upload_video(library)
    library.post(f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.5})

    response = library.delete(f"/api/assets/{parent['id']}")
    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["code"] == "asset_has_dependent_clips"
    assert "1" in body["message"]

    # Refused, not partially done — the parent is still there.
    assert library.get(f"/api/assets/{parent['id']}").status_code == 200


def test_delete_message_counts_more_than_one_clip(library):
    parent = _upload_video(library)
    library.post(f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.2})
    library.post(f"/api/assets/{parent['id']}/clips", json={"in_point": 0.3, "out_point": 0.5})

    body = library.delete(f"/api/assets/{parent['id']}").json()["detail"]
    assert "2 clips depend on" in body["message"]


def test_delete_message_says_depends_for_one_clip(library):
    parent = _upload_video(library)
    _clip(library, parent["id"])

    body = library.delete(f"/api/assets/{parent['id']}").json()["detail"]
    assert "1 clip depends on" in body["message"]


def test_delete_succeeds_with_no_clips(library):
    """Regression check: the guard must not block an ordinary delete."""
    asset = _upload_video(library)
    assert library.delete(f"/api/assets/{asset['id']}").status_code == 204


def test_delete_succeeds_once_the_only_clip_is_promoted(library, session):
    parent = _upload_video(library)
    clip = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    ).json()["data"]

    clip_row = session.get(Asset, clip["id"])
    asset_service.promote_clip(
        session, clip_row, stored=_fake_stored(), thumb_key=None, probe_result=_fake_probe()
    )

    assert library.delete(f"/api/assets/{parent['id']}").status_code == 204

    # The promoted clip survives the parent's delete, and its breadcrumb is cleared —
    # not left dangling at a foreign key the delete would otherwise trip over.
    session.expire_all()
    survivor = session.get(Asset, clip["id"])
    assert survivor is not None
    assert survivor.parent_asset_id is None
    assert survivor.source == "sub_video"


def test_delete_still_blocked_if_only_some_clips_are_promoted(library, session):
    parent = _upload_video(library)
    keep = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.2}
    ).json()["data"]
    promoted = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.3, "out_point": 0.5}
    ).json()["data"]

    promoted_row = session.get(Asset, promoted["id"])
    asset_service.promote_clip(
        session, promoted_row, stored=_fake_stored(), thumb_key=None, probe_result=_fake_probe()
    )

    response = library.delete(f"/api/assets/{parent['id']}")
    assert response.status_code == 409
    assert "1" in response.json()["detail"]["message"]


def test_deleting_a_parent_keeps_its_promoted_childs_poster(library, session, media_dir):
    """A promote whose own thumbnail failed keeps the parent's key (`promote_clip`'s
    fallback), so the survivor's poster is the parent's file. Deleting the parent used
    to unlink it, leaving the sub-video it just let through showing nothing."""
    parent = _upload_video(library)
    poster = _give_a_poster(session, media_dir, parent["id"])
    promoted = _clip(library, parent["id"])
    asset_service.promote_clip(
        session,
        session.get(Asset, promoted["id"]),
        stored=_fake_stored(),
        thumb_key=None,
        probe_result=_fake_probe(),
    )

    assert library.delete(f"/api/assets/{parent['id']}").status_code == 204

    assert poster.exists()
    survivor = library.get(f"/api/assets/{promoted['id']}").json()["data"]
    assert poster.name in survivor["thumb_url"]


# ─── deleting a parent with its clips ─────────────────────────────────────────


def test_delete_with_clips_takes_the_live_clips_and_the_parent(library, session, media_dir):
    parent = _upload_video(library)
    _give_a_poster(session, media_dir, parent["id"])
    first = _clip(library, parent["id"], 0.0, 0.5)
    second = _clip(library, parent["id"], 0.5, 1.0)
    # Tagged, because `AssetTag` is a foreign key: a clip deleted without its tags
    # detached first is a 500, not a 204.
    library.post(f"/api/assets/{first['id']}/tags", json={"names": ["chapter"]})

    response = library.delete(f"/api/assets/{parent['id']}", params={"with_clips": "true"})
    assert response.status_code == 204

    for row in (parent, first, second):
        assert library.get(f"/api/assets/{row['id']}").status_code == 404
    # The file and the shared poster both went: once the parent and every clip were
    # gone, nothing referenced either.
    assert _files(media_dir) == set()


def test_delete_with_clips_keeps_a_promoted_child(library, session, media_dir):
    """`with_clips` deletes exactly what `blocking_clips` returns. A promoted child owns
    its own bytes and only loses the breadcrumb — and keeps the poster it shares."""
    parent = _upload_video(library)
    poster = _give_a_poster(session, media_dir, parent["id"])
    live = _clip(library, parent["id"], 0.0, 0.5)
    promoted = _clip(library, parent["id"], 0.5, 1.0)
    asset_service.promote_clip(
        session,
        session.get(Asset, promoted["id"]),
        stored=_fake_stored(),
        thumb_key=None,
        probe_result=_fake_probe(),
    )

    response = library.delete(f"/api/assets/{parent['id']}", params={"with_clips": "true"})
    assert response.status_code == 204

    assert library.get(f"/api/assets/{live['id']}").status_code == 404
    session.expire_all()
    survivor = session.get(Asset, promoted["id"])
    assert survivor is not None
    assert survivor.parent_asset_id is None
    assert survivor.source == "sub_video"
    assert poster.exists()


def test_delete_with_clips_is_a_plain_delete_when_there_are_none(library):
    asset = _upload_video(library)

    response = library.delete(f"/api/assets/{asset['id']}", params={"with_clips": "true"})
    assert response.status_code == 204
    assert library.get(f"/api/assets/{asset['id']}").status_code == 404


def test_delete_with_clips_is_refused_while_one_is_being_promoted(library):
    """A promote that finished into a deleted row would write a file nothing points at.
    Refused whole: the parent and both clips are still there afterwards."""
    parent = _upload_video(library)
    first = _clip(library, parent["id"], 0.0, 0.5)
    second = _clip(library, parent["id"], 0.5, 1.0)
    # Stays queued: the autouse `no_background_workers` fixture keeps the worker off.
    assert library.post(f"/api/assets/{first['id']}/promote").status_code == 202

    response = library.delete(f"/api/assets/{parent['id']}", params={"with_clips": "true"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "extraction_in_progress"

    for row in (parent, first, second):
        assert library.get(f"/api/assets/{row['id']}").status_code == 200


def test_delete_is_refused_while_a_sub_video_is_being_cut_from_it(library):
    asset = _upload_video(library)
    library.post(f"/api/assets/{asset['id']}/subvideo", json={"in_point": 0.0, "out_point": 0.5})

    response = library.delete(f"/api/assets/{asset['id']}")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "extraction_in_progress"
    assert library.get(f"/api/assets/{asset['id']}").status_code == 200


def test_delete_with_clips_of_someone_elses_asset_is_a_404(library, session):
    other = Asset(user_id="somebody-else", name="Theirs", asset_type="video", source="local_upload")
    session.add(other)
    session.commit()
    their_clip = Asset(
        user_id="somebody-else",
        name="Their clip",
        asset_type="video",
        source="clip",
        parent_asset_id=other.id,
        in_point=0.0,
        out_point=1.0,
    )
    session.add(their_clip)
    session.commit()

    response = library.delete(f"/api/assets/{other.id}", params={"with_clips": "true"})
    assert response.status_code == 404
    assert session.get(Asset, other.id) is not None
    assert session.get(Asset, their_clip.id) is not None


# ─── deleting a clip ───────────────────────────────────────────────────────────


def test_deleting_a_clip_removes_only_the_clip(library, session, media_dir):
    """A clip owns no bytes, and the poster it shows is its parent's: `create_clip`
    copies the key. Deleting one used to unlink that key, blanking the poster of the
    parent and of every sibling clip with it."""
    parent = _upload_video(library)
    poster = _give_a_poster(session, media_dir, parent["id"])
    doomed = _clip(library, parent["id"], 0.0, 0.5)
    sibling = _clip(library, parent["id"], 0.5, 1.0)
    library.post(f"/api/assets/{doomed['id']}/tags", json={"names": ["chapter"]})
    before = _files(media_dir)

    assert library.delete(f"/api/assets/{doomed['id']}").status_code == 204

    assert library.get(f"/api/assets/{doomed['id']}").status_code == 404
    assert library.get(f"/api/assets/{parent['id']}").status_code == 200
    assert not session.exec(select(AssetTag).where(AssetTag.asset_id == doomed["id"])).all()
    # Nothing on disk changed: the parent's file and its poster are both still there.
    assert _files(media_dir) == before
    fetched = library.get(f"/api/assets/{sibling['id']}").json()["data"]
    assert poster.name in fetched["thumb_url"]
    assert poster.exists()


def test_deleting_the_last_clip_unblocks_the_parent(library):
    parent = _upload_video(library)
    clip = _clip(library, parent["id"])
    assert library.delete(f"/api/assets/{parent['id']}").status_code == 409

    assert library.delete(f"/api/assets/{clip['id']}").status_code == 204
    assert library.delete(f"/api/assets/{parent['id']}").status_code == 204


def test_delete_is_refused_while_the_clip_is_being_promoted(library):
    parent = _upload_video(library)
    clip = _clip(library, parent["id"])
    assert library.post(f"/api/assets/{clip['id']}/promote").status_code == 202

    response = library.delete(f"/api/assets/{clip['id']}")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "extraction_in_progress"
    assert library.get(f"/api/assets/{clip['id']}").status_code == 200


def test_a_deleted_clip_leaves_search(library, session):
    """Asserted on the index itself, not only on the results: search already drops a
    hit whose asset cannot be loaded, so a stale entry would not show there."""
    parent = _upload_video(library)
    clip = _clip(library, parent["id"], name="The nano weapons moment")
    hits = library.get("/api/search", params={"q": "nano weapons"}).json()["data"]
    assert clip["id"] in {hit["asset"]["id"] for hit in hits}

    assert library.delete(f"/api/assets/{clip['id']}").status_code == 204

    indexed = session.execute(
        text(f"SELECT count(*) FROM {fts.ASSET_FTS} WHERE asset_id = :id"), {"id": clip["id"]}
    ).one()[0]
    assert indexed == 0


# ─── the gather() guard (M6 enrichment must refuse a clip) ────────────────────


def test_gather_refuses_a_clip(library, session):
    parent = _upload_video(library)
    clip = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    ).json()["data"]
    clip_row = session.get(Asset, clip["id"])

    with pytest.raises(NoSourceMaterial, match="clip"):
        source.gather(session, clip_row, supports_images=True)


def test_gather_still_works_on_a_promoted_clip(library, session):
    """The guard keys off `storage_key`, not `parent_asset_id` — a promoted clip owns
    real bytes again and must not be refused as a clip."""
    parent = _upload_video(library)
    clip = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    ).json()["data"]
    clip_row = session.get(Asset, clip["id"])
    asset_service.promote_clip(
        session, clip_row, stored=_fake_stored(), thumb_key=None, probe_result=_fake_probe()
    )
    session.refresh(clip_row)
    # Whether it also inherited a poster from its parent is incidental to what this
    # test checks (that promoting lifts the clip-specific refusal) and depends on
    # whether ffmpeg is on PATH in whatever environment runs this — cleared explicitly
    # so the rest of the assertion is deterministic either way.
    clip_row.thumb_key = None
    session.add(clip_row)
    session.commit()
    session.refresh(clip_row)

    with pytest.raises(NoSourceMaterial, match="Transcribe"):
        # No transcript, no poster, no document text — refused for the ordinary reason
        # every untranscribed video is, not the clip-specific one.
        source.gather(session, clip_row, supports_images=True)


# ─── running the job for real (needs ffmpeg — skipped in this sandbox) ────────


@needs_ffmpeg
def test_running_extract_creates_a_standalone_asset(library, session):
    import json

    from app.enrichment.extract_subvideo import run as run_extract_subvideo

    created = _upload_video(library)
    asset = session.get(Asset, created["id"])
    payload = json.dumps({"mode": "extract", "in_point": 0.2, "out_point": 1.5, "name": "Cut"})

    detail, created_id = run_extract_subvideo(session, asset, payload, lambda *a, **k: None)

    assert created_id is not None
    subvideo = session.get(Asset, created_id)
    assert subvideo.source == "sub_video"
    assert subvideo.storage_key is not None
    assert subvideo.parent_asset_id == asset.id
    assert subvideo.in_point is None and subvideo.out_point is None
    # The source asset is genuinely untouched.
    session.refresh(asset)
    assert asset.storage_key == created["file_url"].split("?")[0].removeprefix("/media/")


@needs_ffmpeg
def test_running_promote_updates_the_clip_in_place(library, session):
    import json

    from app.enrichment.extract_subvideo import run as run_extract_subvideo

    parent = _upload_video(library)
    clip = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.2, "out_point": 1.5}
    ).json()["data"]
    clip_row = session.get(Asset, clip["id"])
    clip_id = clip_row.id

    detail, created_id = run_extract_subvideo(
        session, clip_row, json.dumps({"mode": "promote"}), lambda *a, **k: None
    )

    assert created_id is None  # promote updates in place; nothing new to report
    session.expire_all()
    promoted = session.get(Asset, clip_id)
    assert promoted.source == "sub_video"
    assert promoted.storage_key is not None
    assert promoted.in_point is None and promoted.out_point is None
    assert promoted.parent_asset_id == parent["id"]  # provenance survives


@needs_ffmpeg
def test_the_api_end_to_end_via_the_job_dispatcher(library, session, monkeypatch):
    """The same path a real request takes: submit through the router, then run the
    worker's own dispatch — not `run_extract_subvideo` called directly — so the
    KIND_EXTRACT_SUBVIDEO branch in `jobs/enrichment.py` is what's under test, and so
    is `result_asset_id` reaching `/api/activity` through `jobs/registry.py`."""
    import json

    asset = _upload_video(library)
    job = library.post(
        f"/api/assets/{asset['id']}/subvideo", json={"in_point": 0.1, "out_point": 1.0}
    ).json()["data"]

    # `_run_job` opens its own session from the queue's engine, not this fixture's —
    # matches test_transcripts.py::test_a_queue_cancel_still_leaves_a_terminal_row.
    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job["id"])

    session.expire_all()
    row = session.get(EnrichmentJob, job["id"])
    assert row.status == "done"
    assert row.payload is not None
    created_id = json.loads(row.payload)["created_asset_id"]

    activity = library.get("/api/activity").json()
    matching = next(j for j in activity["data"] if j["id"] == job["id"])
    assert matching["result_asset_id"] == created_id
