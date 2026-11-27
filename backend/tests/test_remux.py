"""Repairing a recording whose container has no duration (the remux job and its route).

Gecko Notes records in the browser, which writes WebM as a live stream. Files already in
a library from before `probe()` learned to measure them have no duration, and no index to
seek by. The repair rewrites the file with the same streams (`-c copy`) and swaps it in
behind the same asset id.
"""

import subprocess
from pathlib import Path

import pytest

from app.enrichment.remux import RemuxError, check, copy_streams, run as run_remux
from app.ingest.probe import ProbeResult, probe
from app.jobs.runner import JobCancelled
from app.media_tools import ffmpeg_available
from app.models.asset import Asset
from app.storage import LocalStorage

TEST_USER = "user-under-test"
needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
FIXTURES = Path(__file__).parent / "fixtures"


def _live_webm(path: Path, seconds: int = 8) -> Path:
    """Written to a pipe, as a recorder does: the muxer cannot go back and fill in a length."""
    with path.open("wb") as sink:
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-nostdin",
                "-f", "lavfi", "-i", f"testsrc=size=160x90:rate=25:d={seconds}",
                "-f", "lavfi", "-i", f"sine=d={seconds}",
                "-c:v", "libvpx", "-b:v", "100k", "-c:a", "libvorbis", "-f", "webm", "pipe:1",
            ],
            stdout=sink, check=True,
        )
    return path


def _upload(client, path: Path, mime: str) -> dict:
    response = client.post("/api/assets", files=[("files", (path.name, path.read_bytes(), mime))])
    assert response.status_code == 201, response.text
    return response.json()["created"][0]


def _as_stored_before_the_fix(session, asset_id: str) -> Asset:
    """What a library already holds: the file arrived, and no duration was recorded."""
    row = session.get(Asset, asset_id)
    row.duration_seconds = None
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _no_progress(*_a, **_k) -> None:
    return None


def _header_duration(path: Path) -> str:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return out.stdout.strip()


# ─── the route ─────────────────────────────────────────────────────────────────


def test_repairing_a_recording_queues_a_job(library):
    video = _upload(library, FIXTURES / "sample_video.mp4", "video/mp4")

    response = library.post(f"/api/assets/{video['id']}/remux")

    assert response.status_code == 202, response.text
    job = response.json()["data"]
    assert (job["action"], job["asset_id"]) == ("remux", video["id"])


def test_only_one_repair_runs_at_a_time(library):
    video = _upload(library, FIXTURES / "sample_video.mp4", "video/mp4")
    assert library.post(f"/api/assets/{video['id']}/remux").status_code == 202

    again = library.post(f"/api/assets/{video['id']}/remux")

    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "remux_in_progress"


def test_a_repair_in_progress_holds_back_a_cut_and_a_version(library, session):
    video = _upload(library, FIXTURES / "sample_video.mp4", "video/mp4")
    row = session.get(Asset, video["id"])
    row.width, row.height = 3840, 2160    # big enough that a smaller version is on offer
    session.add(row)
    session.commit()
    assert library.post(f"/api/assets/{video['id']}/remux").status_code == 202

    cut = library.post(
        f"/api/assets/{video['id']}/subvideo", json={"in_point": 0, "out_point": 1, "mode": "extract"}
    )
    version = library.post(f"/api/assets/{video['id']}/versions", json={"short_side": 1080})

    assert cut.status_code == 409 and cut.json()["detail"]["code"] == "remux_in_progress"
    assert version.status_code == 409 and version.json()["detail"]["code"] == "remux_in_progress"


def test_a_picture_cannot_be_repaired(library):
    pictures = list(FIXTURES.glob("*.png")) + list(FIXTURES.glob("*.jpg"))
    if not pictures:
        pytest.skip("no picture fixture")
    picture = _upload(library, pictures[0], "image/png" if pictures[0].suffix == ".png" else "image/jpeg")

    response = library.post(f"/api/assets/{picture['id']}/remux")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "not_remuxable"


def test_someone_elses_asset_is_not_found(library):
    assert library.post("/api/assets/nope/remux").status_code == 404


# ─── the job, for real ─────────────────────────────────────────────────────────


@needs_ffmpeg
def test_the_job_gives_the_file_a_duration_and_keeps_the_asset(library, session, media_dir, tmp_path):
    video = _upload(library, _live_webm(tmp_path / "rec.webm"), "video/webm")
    row = _as_stored_before_the_fix(session, video["id"])
    old_key, old_checksum = row.storage_key, row.checksum_sha256
    old_path = media_dir / old_key
    assert _header_duration(old_path) == "N/A"      # what the library is holding

    detail = run_remux(session, row, _no_progress)

    session.refresh(row)
    assert detail == "0:08 long"
    assert row.id == video["id"]
    assert row.duration_seconds == pytest.approx(8.0, abs=0.3)
    assert row.storage_key != old_key and row.checksum_sha256 != old_checksum
    new_path = media_dir / row.storage_key
    assert new_path.suffix == ".webm"
    assert float(_header_duration(new_path)) == pytest.approx(8.0, abs=0.3)
    assert row.size_bytes == new_path.stat().st_size
    # The old file goes once nothing points at it, and the served file is the new one.
    assert not old_path.exists()
    assert library.get(library.get(f"/api/assets/{video['id']}").json()["data"]["file_url"]).status_code == 200


@needs_ffmpeg
def test_a_file_that_cannot_be_rewritten_is_left_exactly_as_it_was(library, session, media_dir, tmp_path):
    video = _upload(library, _live_webm(tmp_path / "rec.webm"), "video/webm")
    row = _as_stored_before_the_fix(session, video["id"])
    old_key, old_size = row.storage_key, row.size_bytes
    (media_dir / old_key).write_bytes(b"this is not a video")
    files_before = sorted(p.name for p in (media_dir / TEST_USER).iterdir())

    with pytest.raises(RemuxError, match="unchanged"):
        run_remux(session, row, _no_progress)

    session.refresh(row)
    assert row.storage_key == old_key and row.size_bytes == old_size
    assert sorted(p.name for p in (media_dir / TEST_USER).iterdir()) == files_before


@needs_ffmpeg
def test_a_copy_that_fails_the_check_is_never_swapped_in(library, session, media_dir, tmp_path, monkeypatch):
    from app.enrichment import remux

    video = _upload(library, _live_webm(tmp_path / "rec.webm"), "video/webm")
    row = _as_stored_before_the_fix(session, video["id"])
    old_key = row.storage_key
    files_before = sorted(p.name for p in (media_dir / TEST_USER).iterdir())

    def refuse(before, after):
        raise RemuxError("different length")

    monkeypatch.setattr(remux, "check", refuse)
    with pytest.raises(RemuxError):
        run_remux(session, row, _no_progress)

    session.refresh(row)
    assert row.storage_key == old_key
    assert sorted(p.name for p in (media_dir / TEST_USER).iterdir()) == files_before


@needs_ffmpeg
def test_cancelling_stops_ffmpeg_and_changes_nothing(library, session, media_dir, tmp_path, monkeypatch):
    from app.enrichment import remux

    monkeypatch.setattr(remux, "_REPORT_EVERY_SECONDS", 0.0)
    video = _upload(library, _live_webm(tmp_path / "rec.webm", seconds=20), "video/webm")
    row = _as_stored_before_the_fix(session, video["id"])
    old_key = row.storage_key
    files_before = sorted(p.name for p in (media_dir / TEST_USER).iterdir())

    def cancelled(stage, percent, detail):
        # Only the copy's own reports, so the cancel lands while ffmpeg is running.
        if stage == "Repairing" and percent >= 1:
            raise JobCancelled("job")

    with pytest.raises(JobCancelled):
        run_remux(session, row, cancelled)

    session.refresh(row)
    assert row.storage_key == old_key
    assert sorted(p.name for p in (media_dir / TEST_USER).iterdir()) == files_before


def test_a_full_disk_stops_a_queued_repair(library, session, disk):
    video = _upload(library, FIXTURES / "sample_video.mp4", "video/mp4")
    disk.set(free_gb=1)
    with pytest.raises(RemuxError, match="disk is nearly full"):
        run_remux(session, session.get(Asset, video["id"]), _no_progress)


# ─── the check ─────────────────────────────────────────────────────────────────


def _result(duration, video=True, audio=True) -> ProbeResult:
    return ProbeResult(duration_seconds=duration, has_video=video, has_audio=audio)


def test_a_copy_that_lost_a_track_is_refused():
    with pytest.raises(RemuxError, match="lost a track"):
        check(_result(60.0), _result(60.0, audio=False))


def test_a_copy_with_no_length_is_refused():
    with pytest.raises(RemuxError, match="no length"):
        check(_result(60.0), _result(None))


def test_a_copy_of_a_different_length_is_refused():
    with pytest.raises(RemuxError, match="different length"):
        check(_result(1800.0), _result(8.0))


def test_a_copy_within_a_frame_or_two_is_accepted():
    check(_result(1800.0), _result(1800.04))
    check(_result(8.0), _result(8.9))


def test_a_copy_is_accepted_when_the_original_could_not_be_measured():
    check(_result(None), _result(1800.0))
