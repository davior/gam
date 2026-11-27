"""A recording whose header has no duration is measured, not stored as lengthless.

Gecko Notes records in the browser, and a browser writes WebM as a live stream: no
duration and no seek index, so ffprobe says `N/A`. Everything downstream (the player's
seek bar, clip making, Video Creator's renderer) then had no length for a 30-minute
recording.
"""

import subprocess
from pathlib import Path

import pytest

from app.ingest import probe as probe_module
from app.ingest.probe import ProbeResult, probe
from app.media_tools import ffmpeg_available

needs_ffmpeg = pytest.mark.skipif(
    not ffmpeg_available(), reason="ffmpeg/ffprobe not on PATH"
)


def _live_webm(path: Path, seconds: int) -> Path:
    """Written to a pipe, as a recorder does: the muxer cannot seek back to fill in a length."""
    with path.open("wb") as sink:
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-nostdin",
                "-f", "lavfi", "-i", f"testsrc=size=160x90:rate=25:d={seconds}",
                "-f", "lavfi", "-i", f"sine=d={seconds}",
                "-c:v", "libvpx", "-b:v", "100k", "-c:a", "libvorbis",
                "-f", "webm", "pipe:1",
            ],
            stdout=sink, check=True,
        )
    return path


@needs_ffmpeg
def test_a_live_written_webm_gets_its_length_from_its_packets(tmp_path):
    path = _live_webm(tmp_path / "recording.webm", 6)

    result = probe(path)

    assert result.has_video and result.has_audio
    assert result.duration_seconds == pytest.approx(6.0, abs=0.25)
    assert (result.width, result.height) == (160, 90)


@needs_ffmpeg
def test_a_file_with_a_header_duration_is_not_scanned(tmp_path, monkeypatch):
    path = tmp_path / "normal.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi",
            "-i", "testsrc=size=160x90:rate=25:d=2", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )
    monkeypatch.setattr(
        probe_module, "_scan_duration", lambda *a: pytest.fail("a healthy header needs no scan")
    )

    assert probe(path).duration_seconds == pytest.approx(2.0, abs=0.1)


@needs_ffmpeg
def test_a_picture_is_not_scanned(tmp_path, monkeypatch):
    path = tmp_path / "still.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
         "color=c=red:size=32x32", "-frames:v", "1", str(path)],
        check=True,
    )
    monkeypatch.setattr(
        probe_module, "_scan_duration", lambda *a: pytest.fail("a picture has no length to find")
    )

    probe(path)


def test_an_absurd_header_duration_is_replaced_by_a_longer_measured_one(tmp_path, monkeypatch):
    """555 MB in "8 seconds" is 555 Mbit/s: the header is wrong, not the file huge."""
    path = tmp_path / "big.webm"
    path.write_bytes(b"x" * 1000)
    monkeypatch.setattr(probe_module, "IMPLAUSIBLE_BYTES_PER_SECOND", 10)
    monkeypatch.setattr(probe_module, "_scan_duration", lambda *a: 1800.0)
    header = ProbeResult(duration_seconds=8.0, has_video=True)

    repaired = probe_module._repair_duration(path, {"format": {"format_name": "matroska,webm"}}, header)

    assert repaired.duration_seconds == 1800.0


def test_a_scan_never_shortens_a_header_duration(tmp_path, monkeypatch):
    path = tmp_path / "big.webm"
    path.write_bytes(b"x" * 1000)
    monkeypatch.setattr(probe_module, "IMPLAUSIBLE_BYTES_PER_SECOND", 10)
    monkeypatch.setattr(probe_module, "_scan_duration", lambda *a: 3.0)
    header = ProbeResult(duration_seconds=8.0, has_video=True)

    repaired = probe_module._repair_duration(path, {"format": {"format_name": "matroska,webm"}}, header)

    assert repaired.duration_seconds == 8.0
