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
