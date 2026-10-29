"""Re-encoding a video to a smaller frame, as a new asset."""

import json
import subprocess
from pathlib import Path

import pytest
from sqlmodel import select

from app.enrichment.reencode import ReencodeError, encode, run as run_reencode
from app.ingest.probe import probe
from app.jobs.runner import JobCancelled
from app.media_tools import ffmpeg_available
from app.models.asset import Asset
from app.models.job import EnrichmentJob, KIND_REENCODE
from app.models.transcript import TranscriptSegment

FIXTURES = Path(__file__).parent / "fixtures"

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


def _upload(client, path: Path) -> dict:
    return client.post(
        "/api/assets", files=[("files", (path.name, path.read_bytes(), "video/mp4"))]
    ).json()["created"][0]


def _upload_fixture(client, name: str, mime: str) -> dict:
    return client.post(
        "/api/assets", files=[("files", (name, (FIXTURES / name).read_bytes(), mime))]
    ).json()["created"][0]


def _pretend_size(session, asset_id: str, width: int, height: int) -> None:
    row = session.get(Asset, asset_id)
    row.width, row.height = width, height
    session.add(row)
    session.commit()


# ─── the route ─────────────────────────────────────────────────────────────────


def test_reencode_queues_a_job(library, session):
    video = _upload_fixture(library, "sample_video.mp4", "video/mp4")
    _pretend_size(session, video["id"], 3840, 2160)

    response = library.post(f"/api/assets/{video['id']}/reencode", json={"short_side": 1080})
    assert response.status_code == 202
    job = response.json()["data"]
    assert job["action"] == "reencode"
    assert job["asset_id"] == video["id"]


def test_reencode_refuses_a_size_that_is_not_smaller(library):
    # The fixture is 320x240: nothing on the list is smaller.
    video = _upload_fixture(library, "sample_video.mp4", "video/mp4")
    response = library.post(f"/api/assets/{video['id']}/reencode", json={"short_side": 360})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "not_smaller"


def test_reencode_refuses_an_unlisted_size(library, session):
    video = _upload_fixture(library, "sample_video.mp4", "video/mp4")
    _pretend_size(session, video["id"], 3840, 2160)
    response = library.post(f"/api/assets/{video['id']}/reencode", json={"short_side": 1000})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "bad_request"


def test_reencode_refuses_audio(library):
    audio = _upload_fixture(library, "sample_audio.mp3", "audio/mpeg")
    response = library.post(f"/api/assets/{audio['id']}/reencode", json={"short_side": 720})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "not_reencodable"


def test_reencode_refuses_a_second_concurrent_run(library, session):
    video = _upload_fixture(library, "sample_video.mp4", "video/mp4")
    _pretend_size(session, video["id"], 3840, 2160)
    library.post(f"/api/assets/{video['id']}/reencode", json={"short_side": 1080})

    response = library.post(f"/api/assets/{video['id']}/reencode", json={"short_side": 720})
    assert response.status_code == 409


def test_reencode_of_someone_elses_video_is_a_404(library, session):
    other = Asset(user_id="somebody-else", name="Theirs", asset_type="video", source="local_upload")
    session.add(other)
    session.commit()
    response = library.post(f"/api/assets/{other.id}/reencode", json={"short_side": 720})
    assert response.status_code == 404


def test_a_running_reencode_blocks_deleting_its_source(library, session):
    video = _upload_fixture(library, "sample_video.mp4", "video/mp4")
    _pretend_size(session, video["id"], 3840, 2160)
    library.post(f"/api/assets/{video['id']}/reencode", json={"short_side": 1080})

    response = library.delete(f"/api/assets/{video['id']}")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "extraction_in_progress"


# ─── running it for real ───────────────────────────────────────────────────────


@needs_ffmpeg
def test_running_creates_a_smaller_standalone_copy(library, session, tmp_path):
    video = _upload(library, _make_video(tmp_path / "wide.mp4", 1280, 720))
    source = session.get(Asset, video["id"])
    source_key = source.storage_key

    detail, created_id = run_reencode(
        session, source, json.dumps({"short_side": 360}), lambda *a, **k: None
    )

    assert detail == "360p copy"
    copy = session.get(Asset, created_id)
    assert (copy.width, copy.height) == (640, 360)
    assert copy.source == "reencode"
    assert copy.parent_asset_id == source.id
    assert copy.storage_key and copy.storage_key != source_key
    assert copy.name == f"{source.name} (360p)"
    # The original is untouched.
    session.refresh(source)
    assert source.storage_key == source_key
    assert (source.width, source.height) == (1280, 720)


@needs_ffmpeg
def test_the_copy_keeps_everything_once_the_original_is_deleted(library, session, tmp_path):
    """The workflow this exists for: downsize, then delete the original for the space."""
    video = _upload(library, _make_video(tmp_path / "wide.mp4", 1280, 720))
    library.patch(
        f"/api/assets/{video['id']}",
        json={"description": "Harbour at dawn", "creator": "A. Filmer", "publisher": "Gecko"},
    )
    r = library.post(f"/api/assets/{video['id']}/tags", json={"names": ["harbour", "b-roll"]})
    assert r.status_code == 200, r.text
    source = session.get(Asset, video["id"])
    source.transcript_status = "done"
    session.add(source)
    session.add(
        TranscriptSegment(
            asset_id=source.id, user_id=source.user_id, idx=0,
            text="the boats come in", start_time=0.1, end_time=0.9,
        )
    )
    session.commit()

    _, created_id = run_reencode(
        session, source, json.dumps({"short_side": 360}), lambda *a, **k: None
    )
    assert library.delete(f"/api/assets/{video['id']}").status_code in (200, 204)

    copy = library.get(f"/api/assets/{created_id}").json()["data"]
    assert copy["parent_asset_id"] is None  # the delete cleared the breadcrumb
    assert copy["description"] == "Harbour at dawn"
    assert copy["creator"] == "A. Filmer"
    assert copy["publisher"] == "Gecko"
    assert sorted(t["name"] for t in copy["tags"]) == ["b-roll", "harbour"]
    segments = session.exec(
        select(TranscriptSegment).where(TranscriptSegment.asset_id == created_id)
    ).all()
    assert [s.text for s in segments] == ["the boats come in"]
    # Found by what was said, not only by its name.
    hits = library.get("/api/search", params={"q": "boats"}).json()
    assert created_id in json.dumps(hits)


@needs_ffmpeg
def test_the_target_is_the_short_side_of_a_portrait_video(tmp_path):
    source = _make_video(tmp_path / "tall.mp4", 720, 1280)
    target = tmp_path / "out.mp4"

    encode(source, target, 360, 1.0, lambda *a, **k: None)

    result = probe(target)
    assert (result.width, result.height) == (360, 640)


@needs_ffmpeg
def test_an_odd_aspect_rounds_to_an_even_width(tmp_path):
    # 1000x750 at 360 tall is 480 wide; 1001x750 would be 480.48 — H.264 needs even.
    source = _make_video(tmp_path / "odd.mp4", 1002, 750)
    target = tmp_path / "out.mp4"

    encode(source, target, 360, 1.0, lambda *a, **k: None)

    result = probe(target)
    assert result.height == 360
    assert result.width % 2 == 0


@needs_ffmpeg
def test_cancelling_stops_ffmpeg_and_creates_nothing(library, session, tmp_path, monkeypatch):
    from app.enrichment import reencode

    # Report on every line, so the cancel lands while ffmpeg is still running.
    monkeypatch.setattr(reencode, "_REPORT_EVERY_SECONDS", 0.0)
    video = _upload(library, _make_video(tmp_path / "long.mp4", 1280, 720, seconds=20))
    source = session.get(Asset, video["id"])

    def cancelled(*_args, **_kwargs):
        raise JobCancelled("job")

    with pytest.raises(JobCancelled):
        run_reencode(session, source, json.dumps({"short_side": 360}), cancelled)
    assert session.exec(select(Asset).where(Asset.source == "reencode")).all() == []


def test_a_garbage_file_is_a_readable_error(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")
    with pytest.raises(ReencodeError, match="Could not re-encode"):
        encode(junk, tmp_path / "out.mp4", 360, 1.0, lambda *a, **k: None)


def test_a_full_disk_stops_a_queued_reencode(library, session, disk):
    video = _upload_fixture(library, "sample_video.mp4", "video/mp4")
    source = session.get(Asset, video["id"])
    disk.set(free_gb=1)

    with pytest.raises(ReencodeError, match="disk is nearly full"):
        run_reencode(session, source, json.dumps({"short_side": 360}), lambda *a, **k: None)


def test_the_finished_job_reports_the_new_asset(library, session):
    from app.jobs.registry import KINDS

    video = _upload_fixture(library, "sample_video.mp4", "video/mp4")
    job = EnrichmentJob(
        user_id="user-under-test",
        asset_id=video["id"],
        kind=KIND_REENCODE,
        status="done",
        payload=json.dumps({"short_side": 720, "created_asset_id": "made-this"}),
    )
    session.add(job)
    session.commit()
    assert KINDS["enrichment"].to_activity(job).result_asset_id == "made-this"
