"""Cutting a time range out of a video or audio file.

Two ffmpeg strategies, tried in order. The fast one seeks on the input side and
stream-copies — no re-encode, so it runs in roughly the time it takes to read the
bytes — but a stream copy can only start on a keyframe, so the seek lands at the
nearest one at or before `in_point`, not exactly on it. For most footage that is a
fraction of a second and nobody notices; for content with sparse keyframes (a screen
recording, a slideshow-style video) it can be seconds off, which is wrong enough to
matter for a "cut exactly this moment" feature. So the fast output is checked against
the requested duration, and only re-encoded — output-side seeking, frame-accurate,
slower — when it actually missed by enough to matter.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from app.ingest.probe import probe
from app.media_tools import ffmpeg_available

logger = logging.getLogger(__name__)

# How far the fast path's actual duration may overshoot the requested one before it is
# considered inaccurate enough to redo. Generous relative to a typical keyframe
# interval (2-10s on most encoders), stingy relative to what "cut at 01:01" promises.
DURATION_TOLERANCE_SECONDS = 0.5


class SubvideoExtractionError(Exception):
    """Something the user can be told about."""


# Same shape as `enrichment/audio.py::timeout_for`: scales with the length of the cut,
# not the source file, since that is what ffmpeg actually has to process either way.
def timeout_for(duration_seconds: float | None) -> int:
    if not duration_seconds or duration_seconds <= 0:
        return 300
    return int(min(max(duration_seconds * 2, 120), 3600))


def extract_subvideo(source: Path, target: Path, in_point: float, out_point: float) -> Path:
    """Write `source`'s [in_point, out_point) range to `target`.

    Takes absolute coordinates against a real (non-clip) source — resolving a clip's
    own in/out against its parent is the caller's job, not this function's, and M7 does
    not need it (clipping a clip is out of scope; see `services/assets.py::create_clip`).
    """
    if not ffmpeg_available():
        raise SubvideoExtractionError("ffmpeg is not available on this server")
    if out_point <= in_point:
        raise SubvideoExtractionError("The out point must be after the in point")

    duration = out_point - in_point
    target.parent.mkdir(parents=True, exist_ok=True)
    timeout = timeout_for(duration)

    if _fast_copy(source, target, in_point, duration, timeout):
        actual = probe(target).duration_seconds
        if actual is not None and actual <= duration + DURATION_TOLERANCE_SECONDS:
            return target
        logger.info(
            "Stream-copy cut of %s overshot (wanted %.2fs, got %s) — re-encoding for accuracy",
            source.name, duration, actual,
        )

    _reencode(source, target, in_point, out_point, timeout)
    return target


def _fast_copy(source: Path, target: Path, in_point: float, duration: float, timeout: int) -> bool:
    """Input-side seek plus stream copy. Returns whether it produced a usable file."""
    argv = [
        "ffmpeg", "-nostdin",
        # Before -i: ffmpeg seeks to the nearest keyframe rather than decoding and
        # discarding everything up to it, which is the whole point of the fast path.
        "-ss", f"{in_point:.3f}",
        "-i", str(source),
        "-t", f"{duration:.3f}",
        "-c", "copy",
        # A stream copy starting mid-file otherwise carries the original timestamps,
        # which some players render as a long stall before playback begins.
        "-avoid_negative_ts", "make_zero",
        "-y", str(target),
    ]
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.info("Stream-copy cut of %s failed: %s", source.name, exc)
        return False

    if completed.returncode != 0:
        logger.info(
            "Stream-copy cut of %s failed: %s", source.name, (completed.stderr or "").strip()[-400:]
        )
        return False
    return target.is_file() and target.stat().st_size > 0


def _reencode(source: Path, target: Path, in_point: float, out_point: float, timeout: int) -> None:
    """Output-side seek, frame-accurate, always re-encodes."""
    argv = [
        "ffmpeg", "-nostdin",
        "-i", str(source),
        # After -i: ffmpeg decodes from the start and discards up to the mark, which is
        # what makes this land exactly on `in_point` rather than the nearest keyframe.
        "-ss", f"{in_point:.3f}",
        "-to", f"{out_point:.3f}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-c:a", "aac",
        # Moves the MP4 index to the front of the file, so playback can start before
        # the whole file has downloaded — the same reason every upload gets it free
        # from most modern encoders, made explicit here since libx264 does not default
        # to it.
        "-movflags", "+faststart",
        "-y", str(target),
    ]
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise SubvideoExtractionError("Extracting the sub-video took too long") from exc
    except OSError as exc:
        raise SubvideoExtractionError(f"Could not run ffmpeg: {exc}") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        logger.warning("Sub-video extraction failed for %s: %s", source.name, stderr[-400:])
        raise SubvideoExtractionError("Could not extract that range from this file")

    if not target.is_file() or target.stat().st_size == 0:
        raise SubvideoExtractionError("Could not extract that range from this file")
