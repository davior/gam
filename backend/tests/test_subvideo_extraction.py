"""Cutting a time range out of real media, and the fast-path/re-encode fallback."""

import subprocess
from pathlib import Path

import pytest

from app.enrichment.subvideo import SubvideoExtractionError, extract_subvideo, timeout_for
from app.ingest.probe import probe
from app.media_tools import ffmpeg_available

FIXTURES = Path(__file__).parent / "fixtures"

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


@needs_ffmpeg
def test_extracts_the_requested_range(tmp_path):
    target = tmp_path / "clip.mp4"
    extract_subvideo(FIXTURES / "sample_video.mp4", target, 0.2, 1.5)

    assert target.is_file()
    assert target.stat().st_size > 0
    result = probe(target)
    assert result.duration_seconds == pytest.approx(1.3, abs=0.5)


@needs_ffmpeg
def test_audio_source_works_too(tmp_path):
    target = tmp_path / "clip.m4a"
    extract_subvideo(FIXTURES / "sample_audio.mp3", target, 0.2, 1.2)

    assert target.is_file()
    result = probe(target)
    assert result.duration_seconds == pytest.approx(1.0, abs=0.5)


@needs_ffmpeg
def test_falls_back_to_reencode_when_the_fast_path_overshoots(tmp_path):
    """The case `subvideo.py`'s whole two-strategy design exists for.

    A keyframe only every 10s means the fast path — which can only start on one — has
    to begin at t=0 and run past the requested end to reach it, wildly overshooting a
    0.5s cut. The re-encode fallback decodes and lands exactly on the boundary instead.
    """
    sparse = tmp_path / "sparse_keyframes.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-nostdin", "-f", "lavfi",
            "-i", "testsrc=size=64x64:rate=10:duration=10",
            "-c:v", "libx264", "-g", "100", "-keyint_min", "100",
            "-pix_fmt", "yuv420p", str(sparse),
        ],
        capture_output=True, check=True,
    )

    target = tmp_path / "clip.mp4"
    extract_subvideo(sparse, target, 5.0, 5.5)

    result = probe(target)
    assert result.duration_seconds == pytest.approx(0.5, abs=0.5)


@needs_ffmpeg
def test_fast_path_is_used_when_it_is_already_accurate(tmp_path):
    """The counterpart to the fallback test: dense keyframes (one a second) mean the
    fast path lands close enough on its own, and re-encoding would just be slower for
    no benefit."""
    dense = tmp_path / "dense_keyframes.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-nostdin", "-f", "lavfi",
            "-i", "testsrc=size=64x64:rate=10:duration=10",
            "-c:v", "libx264", "-g", "10", "-keyint_min", "10",
            "-pix_fmt", "yuv420p", str(dense),
        ],
        capture_output=True, check=True,
    )

    target = tmp_path / "clip.mp4"
    extract_subvideo(dense, target, 5.0, 5.5)

    result = probe(target)
    assert result.duration_seconds == pytest.approx(0.5, abs=0.5)


@needs_ffmpeg
def test_refuses_a_backwards_range(tmp_path):
    with pytest.raises(SubvideoExtractionError, match="out point"):
        extract_subvideo(FIXTURES / "sample_video.mp4", tmp_path / "clip.mp4", 5.0, 1.0)


@needs_ffmpeg
def test_a_non_media_file_is_refused(tmp_path):
    junk = tmp_path / "notes.txt"
    junk.write_text("not media")

    with pytest.raises(SubvideoExtractionError):
        extract_subvideo(junk, tmp_path / "clip.mp4", 0.0, 1.0)


def test_timeout_scales_with_the_cut_length():
    """Scales with the *cut*, not the source file — that is what ffmpeg actually has
    to process either way, whichever strategy runs."""
    assert timeout_for(None) == 300
    assert timeout_for(0) == 300
    assert timeout_for(10) == 120  # floor
    assert timeout_for(600) == 1200
    assert timeout_for(10_000) == 3600  # ceiling
