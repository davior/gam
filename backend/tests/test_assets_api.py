"""The asset API, exercised over HTTP with real media files."""

from pathlib import Path

import pytest

from app.models.asset import Asset

FIXTURES = Path(__file__).parent / "fixtures"


def _upload(client, *names: str):
    files = [
        ("files", (name, (FIXTURES / name).read_bytes(), "application/octet-stream"))
        for name in names
    ]
    return client.post("/api/assets", files=files)


# ─── upload ──────────────────────────────────────────────────────────────────


def test_upload_stores_and_describes_an_image(library):
    response = _upload(library, "sample_image.jpg")
    assert response.status_code == 201

    body = response.json()
    assert body["rejected"] == []
    asset = body["created"][0]

    # FR 6.1.2: dropped in with name + file only. The name defaults from the filename.
    assert asset["name"] == "sample_image"
    assert asset["original_name"] == "sample_image.jpg"
    assert asset["asset_type"] == "image"
    assert asset["file_format"] == "jpg"
    assert asset["size_bytes"] > 0
    # Probed automatically (FR 6.1.3).
    assert (asset["width"], asset["height"]) == (800, 600)
    assert asset["thumb_url"]
    assert asset["file_url"]
    assert asset["missing"] is False


def test_upload_probes_video_metadata(library):
    asset = _upload(library, "sample_video.mp4").json()["created"][0]

    assert asset["asset_type"] == "video"
    assert asset["codec"] == "h264"
    assert asset["duration_seconds"] == pytest.approx(2.0, abs=0.3)
    assert (asset["width"], asset["height"]) == (320, 240)
    assert asset["thumb_url"], "a video without a poster is unrecognisable in a grid"


def test_upload_handles_audio_without_dimensions(library):
    asset = _upload(library, "sample_audio.mp3").json()["created"][0]

    assert asset["asset_type"] == "audio"
    assert asset["duration_seconds"] == pytest.approx(2.0, abs=0.3)
    assert asset["width"] is None
    assert asset["thumb_url"] is None


def test_upload_generates_a_pdf_preview(library):
    asset = _upload(library, "sample_document.pdf").json()["created"][0]

    assert asset["asset_type"] == "document"
    assert asset["thumb_url"]


def test_bulk_upload_is_partial_success(library):
    """Twenty files where one is unsupported should store nineteen and say why — not
    make the user find the offending file themselves."""
    files = [
        ("files", ("sample_image.jpg", (FIXTURES / "sample_image.jpg").read_bytes(), "image/jpeg")),
        ("files", ("malware.exe", b"MZ\x90\x00", "application/octet-stream")),
        ("files", ("sample_audio.mp3", (FIXTURES / "sample_audio.mp3").read_bytes(), "audio/mpeg")),
    ]
    response = library.post("/api/assets", files=files)
    assert response.status_code == 201

    body = response.json()
    assert len(body["created"]) == 2
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["filename"] == "malware.exe"
    assert body["rejected"][0]["code"] == "unsupported_type"


def test_upload_of_only_unsupported_files_is_an_error(library):
    """Nothing stored is a failed request, not a 201 with an empty list."""
    response = library.post(
        "/api/assets", files=[("files", ("script.sh", b"#!/bin/sh", "text/plain"))]
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_type"


def test_upload_requires_authentication(client):
    response = client.post("/api/assets", files=[("files", ("x.jpg", b"x", "image/jpeg"))])
    assert response.status_code == 401


# ─── listing ─────────────────────────────────────────────────────────────────


def test_list_returns_newest_first(library):
    _upload(library, "sample_image.jpg")
    _upload(library, "sample_audio.mp3")

    body = library.get("/api/assets").json()
    assert body["total"] == 2
    assert [a["asset_type"] for a in body["data"]] == ["audio", "image"]


def test_list_filters_by_type(library):
    _upload(library, "sample_image.jpg", "sample_audio.mp3", "sample_video.mp4")

    body = library.get("/api/assets", params={"asset_type": "video"}).json()
    assert body["total"] == 1
    assert body["data"][0]["asset_type"] == "video"


def test_list_rejects_an_unknown_type(library):
    response = library.get("/api/assets", params={"asset_type": "hologram"})
    assert response.status_code == 400


def test_list_searches_name_and_original_name(library):
    _upload(library, "sample_image.jpg", "sample_audio.mp3")

    body = library.get("/api/assets", params={"q": "audio"}).json()
    assert body["total"] == 1
    assert body["data"][0]["asset_type"] == "audio"


def test_list_paginates(library):
    _upload(library, "sample_image.jpg", "sample_audio.mp3", "sample_video.mp4")

    first = library.get("/api/assets", params={"limit": 2}).json()
    assert len(first["data"]) == 2
    assert first["total"] == 3

    second = library.get("/api/assets", params={"limit": 2, "offset": 2}).json()
    assert len(second["data"]) == 1
    # No overlap between pages.
    assert {a["id"] for a in first["data"]}.isdisjoint({a["id"] for a in second["data"]})


def test_list_only_shows_your_own_assets(library, session):
    _upload(library, "sample_image.jpg")
    session.add(
        Asset(user_id="somebody-else", name="Not yours", asset_type="image", source="local_upload")
    )
    session.commit()

    body = library.get("/api/assets").json()
    assert body["total"] == 1
    assert body["data"][0]["name"] == "sample_image"


# ─── reading one ─────────────────────────────────────────────────────────────


def test_get_one(library):
    created = _upload(library, "sample_image.jpg").json()["created"][0]

    body = library.get(f"/api/assets/{created['id']}").json()
    assert body["data"]["id"] == created["id"]


def test_get_someone_elses_asset_is_a_404_not_a_403(library, session):
    """A 403 would confirm the id exists, which is itself a disclosure."""
    other = Asset(user_id="somebody-else", name="Secret", asset_type="image", source="local_upload")
    session.add(other)
    session.commit()

    assert library.get(f"/api/assets/{other.id}").status_code == 404


def test_missing_file_is_reported_not_hidden(library, media_dir):
    """A row pointing at bytes that vanished should say so rather than render a broken
    image."""
    created = _upload(library, "sample_image.jpg").json()["created"][0]
    for path in Path(media_dir).rglob("*"):
        if path.is_file() and ".thumb" not in path.name:
            path.unlink()

    body = library.get(f"/api/assets/{created['id']}").json()
    assert body["data"]["missing"] is True


# ─── editing ─────────────────────────────────────────────────────────────────


def test_patch_updates_metadata_and_records_human_provenance(library, session):
    created = _upload(library, "sample_image.jpg").json()["created"][0]

    response = library.patch(
        f"/api/assets/{created['id']}",
        json={"name": "Ocean at sunset", "description": "Golden hour over calm water"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "Ocean at sunset"

    # FR 8.1.3: a later AI run reads this to know what a person wrote.
    asset = session.get(Asset, created["id"])
    import json

    provenance = json.loads(asset.field_provenance)
    assert provenance == {"description": "human", "name": "human"}


def test_patch_bumps_only_the_metadata_timestamp(library, session):
    created = _upload(library, "sample_image.jpg").json()["created"][0]
    before = session.get(Asset, created["id"])
    upload_date, modified_date = before.upload_date, before.modified_date

    library.patch(f"/api/assets/{created['id']}", json={"name": "Renamed"})

    session.expire_all()
    after = session.get(Asset, created["id"])
    # FR 7.1.2: uploadDate never changes, and editing metadata is not a file change.
    assert after.upload_date == upload_date
    assert after.modified_date == modified_date
    assert after.metadata_modified_date > modified_date


def test_patch_can_clear_a_field_but_not_blank_the_name(library):
    created = _upload(library, "sample_image.jpg").json()["created"][0]

    cleared = library.patch(f"/api/assets/{created['id']}", json={"description": None})
    assert cleared.status_code == 200
    assert cleared.json()["data"]["description"] is None

    # A nameless asset is unfindable, so a blank name is refused.
    assert library.patch(f"/api/assets/{created['id']}", json={"name": "   "}).status_code == 422


def test_patch_leaves_unmentioned_fields_alone(library):
    created = _upload(library, "sample_image.jpg").json()["created"][0]
    library.patch(f"/api/assets/{created['id']}", json={"description": "first"})

    body = library.patch(f"/api/assets/{created['id']}", json={"name": "Renamed"}).json()
    assert body["data"]["description"] == "first"


# ─── deleting ────────────────────────────────────────────────────────────────


def test_delete_removes_the_row_and_the_bytes(library, media_dir):
    created = _upload(library, "sample_image.jpg").json()["created"][0]
    assert list(Path(media_dir).rglob("*.jpg"))

    assert library.delete(f"/api/assets/{created['id']}").status_code == 204
    assert library.get(f"/api/assets/{created['id']}").status_code == 404
    assert not [p for p in Path(media_dir).rglob("*") if p.is_file()]


def test_delete_someone_elses_asset_is_a_404(library, session):
    other = Asset(user_id="somebody-else", name="Secret", asset_type="image", source="local_upload")
    session.add(other)
    session.commit()

    assert library.delete(f"/api/assets/{other.id}").status_code == 404
    assert session.get(Asset, other.id) is not None


def test_image_has_no_duration_or_codec(library):
    """ffprobe models a still as a one-frame video, reporting codec "mjpeg" and a
    0.04s duration. Carried through, that puts a "0:00" badge on every image tile."""
    asset = _upload(library, "sample_image.jpg").json()["created"][0]

    assert asset["duration_seconds"] is None
    assert asset["codec"] is None
    # The dimensions are real and must survive.
    assert (asset["width"], asset["height"]) == (800, 600)


def test_video_keeps_its_duration_and_codec(library):
    """Guards the fix above from being over-broad."""
    asset = _upload(library, "sample_video.mp4").json()["created"][0]

    assert asset["duration_seconds"] == pytest.approx(2.0, abs=0.3)
    assert asset["codec"] == "h264"
