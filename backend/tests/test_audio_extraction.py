"""Pulling an audio track out, against real media."""

from pathlib import Path

import pytest

from app.enrichment.audio import AudioExtractionError, extract_audio, timeout_for
from app.media_tools import ffmpeg_available

FIXTURES = Path(__file__).parent / "fixtures"

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


@needs_ffmpeg
def test_extracts_audio_from_video(tmp_path):
    target = tmp_path / "audio.flac"
    extract_audio(FIXTURES / "sample_video.mp4", target, duration_seconds=2.0)

    assert target.is_file()
    assert target.stat().st_size > 0


@needs_ffmpeg
def test_output_is_mono_16k_flac(tmp_path):
    """Speech models take one channel at 16 kHz. Sending 48 kHz stereo is three times
    the bytes for no accuracy — and on a ninety-minute interview that is the whole
    difference in upload time."""
    import json
    import subprocess

    target = tmp_path / "audio.flac"
    extract_audio(FIXTURES / "sample_video.mp4", target, duration_seconds=2.0)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(target)],
        capture_output=True,
        text=True,
        check=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]

    assert stream["codec_name"] == "flac"
    assert int(stream["channels"]) == 1
    assert int(stream["sample_rate"]) == 16000


@needs_ffmpeg
def test_the_output_carries_no_video(tmp_path):
    """The reason this step exists: a real interview is mostly pixels nobody will
    listen to, and sending them to a speech API wastes the upload.

    Note this is not a size assertion. FLAC is lossless, so on a short or low-bitrate
    source it can be *larger* than the compressed original — the saving is against a
    real 1080p recording, not against every input. What the code actually guarantees
    is that no video survives, which is what this checks.
    """
    import json
    import subprocess

    target = tmp_path / "audio.flac"
    extract_audio(FIXTURES / "sample_video.mp4", target, duration_seconds=2.0)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(target)],
        capture_output=True,
        text=True,
        check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    assert all(s["codec_type"] == "audio" for s in streams)


@needs_ffmpeg
def test_audio_only_source_works(tmp_path):
    target = tmp_path / "audio.flac"
    extract_audio(FIXTURES / "sample_audio.mp3", target, duration_seconds=2.0)
    assert target.stat().st_size > 0


@needs_ffmpeg
def test_a_file_with_no_audio_says_so(tmp_path):
    """The common, actionable case: somebody asked to transcribe a silent screen
    recording. That should read as a sentence, not as ffmpeg output."""
    import subprocess

    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-nostdin", "-f", "lavfi",
            "-i", "testsrc=size=64x64:rate=10:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent),
        ],
        capture_output=True,
        check=True,
    )

    with pytest.raises(AudioExtractionError, match="no audio track"):
        extract_audio(silent, tmp_path / "out.flac", duration_seconds=1.0)


@needs_ffmpeg
def test_a_non_media_file_is_refused(tmp_path):
    junk = tmp_path / "notes.txt"
    junk.write_text("not media")

    with pytest.raises(AudioExtractionError):
        extract_audio(junk, tmp_path / "out.flac")


def test_timeout_scales_with_length():
    """A flat cap would kill exactly the long recordings this feature exists for."""
    assert timeout_for(None) == 900
    assert timeout_for(0) == 900
    # Short file: the floor applies.
    assert timeout_for(10) == 300
    # Ninety minutes of audio gets 45 minutes to transcode.
    assert timeout_for(5400) == 2700
    # Bounded, so a corrupt duration cannot hang a worker for a day.
    assert timeout_for(10_000_000) == 7200
