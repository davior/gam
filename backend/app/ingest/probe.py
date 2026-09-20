"""Reading a media file's own metadata with ffprobe.

Everything here degrades rather than fails. A file that will not probe is still a
perfectly good asset — it just has no duration or resolution yet, and the user can
still name it, tag it and find it. Refusing an upload because ffprobe disliked it would
break the one promise ingestion makes: drop a file in and it is kept.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from app.media_tools import ffmpeg_available

logger = logging.getLogger(__name__)

# Generous, but bounded: ffprobe on a healthy file is milliseconds. A minute means
# something is wrong, and an upload must not hang on it.
PROBE_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class ProbeResult:
    duration_seconds: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    codec: Optional[str] = None
    has_audio: bool = False
    has_video: bool = False
    # The container's own tag block — artist, copyright, date and friends. ffprobe has
    # been returning these all along (`-show_format` includes them) and this class threw
    # them away; M10's attribution harvester reads them. Keys are lowercased here
    # because containers disagree about case and callers should not have to.
    #
    # `frozen=True` is for immutability, not hashability — a mutable default would be
    # shared across instances, and nothing hashes a ProbeResult.
    tags: Mapping[str, str] = field(default_factory=dict)


def probe(path: Path) -> ProbeResult:
    """Probe a media file, returning empty results rather than raising."""
    if not ffmpeg_available():
        return ProbeResult()

    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ffprobe failed on %s: %s", path.name, exc)
        return ProbeResult()

    if completed.returncode != 0:
        logger.info("ffprobe could not read %s: %s", path.name, completed.stderr.strip()[:200])
        return ProbeResult()

    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError:
        return ProbeResult()

    return _interpret(payload)


def _interpret(payload: dict[str, Any]) -> ProbeResult:
    streams = payload.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    # Prefer the container's duration: a stream can lack one, and for audio the
    # container is usually the only place it appears.
    duration = _float(payload.get("format", {}).get("duration"))
    if duration is None and video:
        duration = _float(video.get("duration"))
    if duration is None and audio:
        duration = _float(audio.get("duration"))

    width = height = None
    codec = None
    if video:
        width, height = _display_dimensions(video)
        codec = video.get("codec_name")
    elif audio:
        codec = audio.get("codec_name")

    return ProbeResult(
        duration_seconds=duration,
        width=width,
        height=height,
        codec=codec,
        has_audio=audio is not None,
        has_video=video is not None,
        tags=_container_tags(payload, video, audio),
    )


def _container_tags(
    payload: dict[str, Any],
    video: Optional[dict[str, Any]],
    audio: Optional[dict[str, Any]],
) -> Mapping[str, str]:
    """The container's tag block, lowercased, format first and streams as a fallback.

    Where a tag lives depends on the container: MP4 and MP3 put artist/copyright/date on
    the format, while some MKV and transport-stream files carry them only on a stream.
    Reading both means the caller does not have to know which it was handed. Format wins,
    because it describes the file rather than one track of it.
    """
    merged: dict[str, str] = {}
    for source in (audio, video, payload.get("format")):
        for key, value in ((source or {}).get("tags") or {}).items():
            if isinstance(value, str) and value.strip():
                merged[str(key).strip().lower()] = value.strip()
    return merged


def _display_dimensions(stream: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    """Width and height as the video will actually be shown.

    Two things make the raw coded dimensions wrong. Phone video carries a rotation in
    its side data, so a portrait clip reports 1920x1080 and would be filed as
    landscape. And anamorphic content has a non-square sample aspect ratio, where the
    stored width is not the displayed width.
    """
    width = _int(stream.get("width"))
    height = _int(stream.get("height"))
    if width is None or height is None:
        return width, height

    # Anamorphic correction, before rotation: sample_aspect_ratio is "num:den".
    sar = stream.get("sample_aspect_ratio") or ""
    if ":" in sar:
        num, _, den = sar.partition(":")
        num_i, den_i = _int(num), _int(den)
        if num_i and den_i and num_i != den_i and den_i > 0:
            width = round(width * num_i / den_i)

    if _rotation_of(stream) in (90, 270):
        width, height = height, width

    return width, height


def _rotation_of(stream: dict[str, Any]) -> int:
    """Rotation in degrees, from either place ffprobe reports it.

    Older ffmpeg puts it in tags.rotate; newer versions use a Display Matrix side-data
    entry with a negative angle. Both appear in the wild, so both are read.
    """
    tag = _int((stream.get("tags") or {}).get("rotate"))
    if tag is not None:
        return abs(tag) % 360

    for entry in stream.get("side_data_list") or []:
        angle = entry.get("rotation")
        if angle is not None:
            try:
                return abs(int(round(float(angle)))) % 360
            except (TypeError, ValueError):
                continue
    return 0


def _float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    # ffprobe reports "N/A" as a string and 0 for streams it could not measure.
    return parsed if parsed > 0 else None


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
