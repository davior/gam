"""Standard-resolution versions of a video: the 1080p, the 720p, beside the original."""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from sqlmodel import select

from app.config import settings
from app.enrichment.versions import VersionError, encode, run as run_make_version
from app.ingest.probe import ProbeResult, probe
from app.jobs.runner import JobCancelled
from app.media_tools import ffmpeg_available
from app.models.asset import Asset
from app.models.version import AssetVersion
from app.services import versions as version_service
from app.storage import LocalStorage
from app.storage.base import StoredFile

FIXTURES = Path(__file__).parent / "fixtures"
TEST_USER = "user-under-test"

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def _make_video(path: Path, width: int, height: int, seconds: float = 1.0) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate=25:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=duration={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
            "-y", str(path),
        ],
        check=True,
    )
    return path


def _upload(client, path: Path, mime: str = "video/mp4") -> dict:
    response = client.post("/api/assets", files=[("files", (path.name, path.read_bytes(), mime))])
    assert response.status_code == 201, response.text
    return response.json()["created"][0]


def _fixture_video(client) -> dict:
    return _upload(client, FIXTURES / "sample_video.mp4")


def _pretend_size(session, asset_id: str, width: int, height: int) -> None:
    row = session.get(Asset, asset_id)
    row.width, row.height = width, height
    session.add(row)
    session.commit()


def _fake_version(session, media_dir: Path, asset_id: str, short_side: int, size: int = 500) -> AssetVersion:
    """A version row with a real file behind it, without spending an ffmpeg run."""
    asset = session.get(Asset, asset_id)
    key = f"{asset.user_id}/{uuid.uuid4()}.mp4"
    stored = LocalStorage(media_dir).write_bytes(key, b"v" * size)
    return version_service.create(
        session,
        asset=asset,
        short_side=short_side,
        stored=stored,
        probe_result=ProbeResult(width=short_side * 16 // 9, height=short_side, codec="h264"),
    )


def _versions(client, asset_id: str) -> list[dict]:
    response = client.get(f"/api/assets/{asset_id}/versions")
    assert response.status_code == 200, response.text
    return response.json()["data"]


# ─── making one (the route) ────────────────────────────────────────────────────


def test_making_a_version_queues_a_job(library, session):
    video = _fixture_video(library)
    _pretend_size(session, video["id"], 3840, 2160)

    response = library.post(f"/api/assets/{video['id']}/versions", json={"short_side": 1080})
    assert response.status_code == 202, response.text
    job = response.json()["data"]
    assert job["action"] == "make_version"
    assert job["asset_id"] == video["id"]


def test_a_size_that_is_not_smaller_is_refused(library):
    # The fixture is 320x240: nothing on the list is smaller.
    video = _fixture_video(library)
    response = library.post(f"/api/assets/{video['id']}/versions", json={"short_side": 360})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "not_smaller"


def test_a_size_off_the_list_is_refused(library, session):
    video = _fixture_video(library)
    _pretend_size(session, video["id"], 3840, 2160)
    response = library.post(f"/api/assets/{video['id']}/versions", json={"short_side": 1000})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "bad_request"


def test_a_size_it_already_has_is_refused(library, session, media_dir):
    video = _fixture_video(library)
    _pretend_size(session, video["id"], 3840, 2160)
    _fake_version(session, media_dir, video["id"], 1080)

    response = library.post(f"/api/assets/{video['id']}/versions", json={"short_side": 1080})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "version_exists"


def test_audio_has_no_versions(library):
    audio = _upload(library, FIXTURES / "sample_audio.mp3", "audio/mpeg")
    response = library.post(f"/api/assets/{audio['id']}/versions", json={"short_side": 720})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "not_versionable"


def test_one_version_at_a_time(library, session):
    video = _fixture_video(library)
    _pretend_size(session, video["id"], 3840, 2160)
    library.post(f"/api/assets/{video['id']}/versions", json={"short_side": 1080})

    response = library.post(f"/api/assets/{video['id']}/versions", json={"short_side": 720})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "already_running"


def test_someone_elses_video_is_a_404(library, session):
    other = Asset(
        user_id="somebody-else", name="Theirs", asset_type="video",
        source="local_upload", storage_key="somebody-else/x.mp4",
    )
    session.add(other)
    session.commit()
    assert library.get(f"/api/assets/{other.id}/versions").status_code == 404
    response = library.post(f"/api/assets/{other.id}/versions", json={"short_side": 720})
    assert response.status_code == 404


def test_a_version_being_made_blocks_deleting_the_video(library, session):
    video = _fixture_video(library)
    _pretend_size(session, video["id"], 3840, 2160)
    library.post(f"/api/assets/{video['id']}/versions", json={"short_side": 1080})

    response = library.delete(f"/api/assets/{video['id']}")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "extraction_in_progress"


# ─── making one (the job, for real) ────────────────────────────────────────────


@needs_ffmpeg
def test_the_job_adds_a_version_and_leaves_the_original_alone(library, session, tmp_path):
    video = _upload(library, _make_video(tmp_path / "wide.mp4", 1280, 720))
    original_key = session.get(Asset, video["id"]).storage_key

    detail = run_make_version(
        session, session.get(Asset, video["id"]), json.dumps({"short_side": 360}),
        lambda *a, **k: None,
    )

    assert detail == "360p version"
    [version] = _versions(library, video["id"])
    assert (version["short_side"], version["width"], version["height"]) == (360, 640, 360)
    assert version["size_bytes"] > 0
    # Served like any asset file.
    assert library.get(version["file_url"]).status_code == 200
    # Still one asset, with the same file.
    assert session.exec(select(Asset).where(Asset.asset_type == "video")).all() == [
        session.get(Asset, video["id"])
    ]
    assert session.get(Asset, video["id"]).storage_key == original_key


@needs_ffmpeg
def test_the_size_is_the_short_side_of_a_portrait_video(tmp_path):
    target = tmp_path / "out.mp4"
    encode(_make_video(tmp_path / "tall.mp4", 720, 1280), target, 360, 1.0, lambda *a, **k: None)
    result = probe(target)
    assert (result.width, result.height) == (360, 640)


@needs_ffmpeg
def test_an_odd_aspect_comes_out_even_with_square_pixels(tmp_path):
    # 1002x750 at 360 high is 480.96 wide. Without setsar=1 ffmpeg writes 480 pixels
    # stretched to display as 481, which is what a probe reports.
    target = tmp_path / "out.mp4"
    encode(_make_video(tmp_path / "odd.mp4", 1002, 750), target, 360, 1.0, lambda *a, **k: None)
    result = probe(target)
    assert (result.width, result.height) == (480, 360)


@needs_ffmpeg
def test_cancelling_stops_ffmpeg_and_saves_nothing(library, session, tmp_path, monkeypatch, media_dir):
    from app.enrichment import versions

    # Report on every line, so the cancel lands while ffmpeg is still running.
    monkeypatch.setattr(versions, "_REPORT_EVERY_SECONDS", 0.0)
    video = _upload(library, _make_video(tmp_path / "long.mp4", 1280, 720, seconds=20))
    files_before = sorted(p.name for p in (media_dir / TEST_USER).iterdir())

    def cancelled(*_args, **_kwargs):
        raise JobCancelled("job")

    with pytest.raises(JobCancelled):
        run_make_version(
            session, session.get(Asset, video["id"]), json.dumps({"short_side": 360}), cancelled
        )
    assert _versions(library, video["id"]) == []
    assert sorted(p.name for p in (media_dir / TEST_USER).iterdir()) == files_before


def test_a_garbage_file_is_a_readable_error(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")
    with pytest.raises(VersionError, match="Could not encode"):
        encode(junk, tmp_path / "out.mp4", 360, 1.0, lambda *a, **k: None)


def test_a_full_disk_stops_a_queued_version(library, session, disk):
    video = _fixture_video(library)
    disk.set(free_gb=1)
    with pytest.raises(VersionError, match="disk is nearly full"):
        run_make_version(
            session, session.get(Asset, video["id"]), json.dumps({"short_side": 360}),
            lambda *a, **k: None,
        )


# ─── deleting ──────────────────────────────────────────────────────────────────


def test_deleting_a_version_removes_its_file(library, session, media_dir):
    video = _fixture_video(library)
    version = _fake_version(session, media_dir, video["id"], 720)
    path = media_dir / version.storage_key

    response = library.delete(f"/api/assets/{video['id']}/versions/{version.id}")

    assert response.status_code == 204
    assert not path.exists()
    assert _versions(library, video["id"]) == []
    assert library.get(f"/api/assets/{video['id']}").json()["data"]["missing"] is False


def test_deleting_the_video_deletes_its_versions(library, session, media_dir):
    video = _fixture_video(library)
    paths = [
        media_dir / _fake_version(session, media_dir, video["id"], size).storage_key
        for size in (720, 480)
    ]

    assert library.delete(f"/api/assets/{video['id']}").status_code == 204

    assert session.exec(select(AssetVersion)).all() == []
    assert not any(path.exists() for path in paths)


# ─── making one the original ───────────────────────────────────────────────────


def test_make_original_swaps_the_file_and_deletes_the_big_one(library, session, media_dir):
    video = _fixture_video(library)
    library.post(f"/api/assets/{video['id']}/tags", json={"names": ["harbour"]})
    clip = library.post(
        f"/api/assets/{video['id']}/clips", json={"in_point": 0.2, "out_point": 1.0}
    ).json()["data"]
    old_path = media_dir / session.get(Asset, video["id"]).storage_key
    small = _fake_version(session, media_dir, video["id"], 720, size=500)
    other = _fake_version(session, media_dir, video["id"], 480, size=300)
    small_key = small.storage_key

    response = library.post(f"/api/assets/{video['id']}/versions/{small.id}/make-original")

    assert response.status_code == 200, response.text
    asset = response.json()["data"]
    assert asset["id"] == video["id"]
    assert (asset["width"], asset["height"], asset["size_bytes"]) == (1280, 720, 500)
    assert [t["name"] for t in asset["tags"]] == ["harbour"]
    assert session.get(Asset, video["id"]).storage_key == small_key
    assert not old_path.exists()
    assert (media_dir / small_key).exists()
    # It is the original now, not a version; the other version stays.
    assert [v["id"] for v in _versions(library, video["id"])] == [other.id]
    # A clip plays the new file, over the same timeline.
    clip_row = library.get(f"/api/assets/{clip['id']}").json()["data"]
    assert small_key in clip_row["file_url"]


def test_make_original_waits_for_a_version_being_made(library, session, media_dir):
    video = _fixture_video(library)
    _pretend_size(session, video["id"], 3840, 2160)
    version = _fake_version(session, media_dir, video["id"], 1080)
    library.post(f"/api/assets/{video['id']}/versions", json={"short_side": 720})

    response = library.post(f"/api/assets/{video['id']}/versions/{version.id}/make-original")
    assert response.status_code == 409


def test_another_videos_version_is_a_404(library, session, media_dir):
    one = _fixture_video(library)
    two = _fixture_video(library)
    version = _fake_version(session, media_dir, one["id"], 720)
    response = library.post(f"/api/assets/{two['id']}/versions/{version.id}/make-original")
    assert response.status_code == 404


# ─── file counts and sizes ─────────────────────────────────────────────────────


def test_versions_count_in_the_library_total(library, session, media_dir):
    video = _fixture_video(library)
    before = library.get("/api/storage").json()["data"]["library"]
    _fake_version(session, media_dir, video["id"], 720, size=500)
    _fake_version(session, media_dir, video["id"], 480, size=300)

    after = library.get("/api/storage").json()["data"]["library"]

    assert after["versions"] == {"files": 2, "bytes": 800}
    assert after["files"] == before["files"] + 2
    assert after["total_bytes"] == before["total_bytes"] + 800
    # The originals' own rows are unchanged: versions are their own group.
    assert after["by_type"] == before["by_type"]


def test_largest_ranks_by_original_plus_versions(library, session, media_dir):
    def video_of(size: int) -> Asset:
        row = Asset(
            user_id=TEST_USER, name=f"v{size}", asset_type="video",
            storage_key=f"{TEST_USER}/{uuid.uuid4()}.mp4", size_bytes=size,
        )
        session.add(row)
        session.commit()
        return row

    big = video_of(1000)
    small_with_versions = video_of(800)
    _fake_version(session, media_dir, small_with_versions.id, 720, size=400)

    largest = library.get("/api/storage").json()["data"]["library"]["largest"]

    assert [row["id"] for row in largest[:2]] == [small_with_versions.id, big.id]
    assert largest[0]["size_bytes"] == 800
    assert largest[0]["versions_bytes"] == 400


def test_a_version_file_is_not_a_stray(library, session, media_dir, monkeypatch):
    monkeypatch.setattr(settings, "admin_users", "tester")
    video = _fixture_video(library)
    version = _fake_version(session, media_dir, video["id"], 720)
    path = media_dir / version.storage_key
    past = time.time() - 3 * 24 * 3600
    os.utime(path, (past, past))

    library.post("/api/storage/cleanup")

    assert path.exists()
