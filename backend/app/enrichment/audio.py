"""Getting an audio track out of a media file.

A ninety-minute 1080p interview is a gigabyte of video carrying a few minutes' worth of
actual speech data. Uploading the video to a speech API spends almost all of that on
pixels nobody will listen to, and on a slow connection it is the difference between a
transcript in two minutes and one in twenty. So video is stripped to mono 16 kHz audio
first, which is also the form speech models are trained on.

Worth being precise about the saving: FLAC is lossless, so on a short or low-bitrate
source the extracted audio can be *larger* than the compressed original. The win is
against real recordings, not against every input — and accuracy is worth more here than
bytes, since a transcript is searched for years after the upload is forgotten.
"""

from __future__ import annotations

import logging
import math
import subprocess
from pathlib import Path

from app.media_tools import ffmpeg_available

logger = logging.getLogger(__name__)


class AudioExtractionError(Exception):
    """The file had no usable audio, or ffmpeg could not read it."""


# Scales with length: a long file genuinely takes longer to transcode, and a flat cap
# would kill exactly the recordings this feature exists for. Floor covers process
# startup on a short clip.
def timeout_for(duration_seconds: float | None) -> int:
    if not duration_seconds or duration_seconds <= 0:
        return 900
    return int(min(max(duration_seconds * 0.5, 300), 7200))


def extract_audio(source: Path, target: Path, duration_seconds: float | None = None) -> Path:
    """Write `source`'s audio to `target` as 16 kHz mono FLAC.

    FLAC rather than MP3: it is lossless, so nothing is thrown away before the speech
    model sees it, and it is roughly half the size of WAV. Mono because speech models
    take a single channel anyway, and 16 kHz because that is the rate they are trained
    at — sending 48 kHz stereo is three times the bytes for no accuracy.
    """
    if not ffmpeg_available():
        raise AudioExtractionError("ffmpeg is not available on this server")

    target.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        "ffmpeg",
        "-nostdin",
        "-i", str(source),
        # Fail rather than produce an empty track if there is no audio stream at all.
        "-vn",
        "-map", "0:a:0",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "flac",
        "-y",
        str(target),
    ]

    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout_for(duration_seconds)
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioExtractionError("Extracting the audio took too long") from exc
    except OSError as exc:
        raise AudioExtractionError(f"Could not run ffmpeg: {exc}") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        # The common, actionable case: somebody asked to transcribe a silent screen
        # recording or a still image. Say that, rather than showing them ffmpeg output.
        if "does not contain any stream" in stderr or "Stream map" in stderr:
            raise AudioExtractionError("This file has no audio track to transcribe")
        logger.warning("Audio extraction failed for %s: %s", source.name, stderr[-400:])
        raise AudioExtractionError("Could not extract audio from this file")

    if not target.is_file() or target.stat().st_size == 0:
        raise AudioExtractionError("This file has no audio track to transcribe")

    return target


def audio_duration(path: Path) -> float:
    """Seconds of audio in `path`, read from the file rather than trusted from the asset:
    the parts' offsets are built from this, and an offset that drifts puts every later
    word at the wrong moment in the player."""
    try:
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return float((completed.stdout or "").strip())
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise AudioExtractionError("Could not read the length of the extracted audio") from exc


def split_audio(source: Path, workdir: Path, part_seconds: float,
                duration_seconds: float | None = None) -> list[tuple[Path, float]]:
    """`source` as pieces of at most `part_seconds`, each with the second it starts at.

    Why pieces at all: one request carrying a long recording outgrows every limit on the
    way (the suite's 300 MB body cap and its nginx in front, read timeouts, Deepgram's own
    processing time); a two-hour YouTube import is what found it. Pieces keep each request
    small at any length. Audio no longer than one piece comes back as itself, untouched,
    so a short file takes exactly the path it always did.

    Each piece is cut on its own (`-ss` before `-i`, re-encoded, which ffmpeg seeks
    sample-accurately) rather than with the segment muxer: the muxer's FLAC pieces carry
    no length in their header and its cut points are only approximately where asked, and
    the offsets here are what put every later word at the right moment in the player.
    """
    total = audio_duration(source)
    if total <= part_seconds:
        return [(source, 0.0)]

    count = math.ceil(total / part_seconds)
    per_part_timeout = timeout_for(part_seconds)
    parts: list[tuple[Path, float]] = []
    for index in range(count):
        offset = index * part_seconds
        target = workdir / f"part-{index:04d}.flac"
        argv = [
            "ffmpeg", "-nostdin", "-ss", f"{offset:.3f}", "-t", f"{part_seconds:.3f}",
            "-i", str(source), "-c:a", "flac", "-y", str(target),
        ]
        try:
            completed = subprocess.run(argv, capture_output=True, text=True, timeout=per_part_timeout)
        except subprocess.TimeoutExpired as exc:
            raise AudioExtractionError("Splitting the audio took too long") from exc
        except OSError as exc:
            raise AudioExtractionError(f"Could not run ffmpeg: {exc}") from exc
        if completed.returncode != 0:
            logger.warning("Splitting audio failed: %s", (completed.stderr or "").strip()[-400:])
            raise AudioExtractionError("Could not split the audio into parts")
        # The last cut can land past the end by a rounding hair and come out empty.
        if target.is_file() and target.stat().st_size > 0 and audio_duration(target) > 0:
            parts.append((target, offset))
    if not parts:
        raise AudioExtractionError("Could not split the audio into parts")
    return parts
