"""The reencode job: a video's bytes in, a smaller copy of it out, as a new asset.

For a high-resolution original that is too heavy to cut with in a 720p or 1080p Video
Creator project. The original is never touched; the copy is an ordinary asset with the
original as `parent_asset_id` (provenance only, so it never blocks a delete).

One ffmpeg command. The only machinery around it is reading ffmpeg's own `-progress`
output, which is not decoration: a 4K hour re-encodes for longer than the stale sweeper's
`job_stale_minutes`, and a job that reports nothing for that long is ended as hung. The
same reports are the cancellation checkpoint, so Cancel stops ffmpeg instead of
leaving it to run to the end on a job nobody is waiting for.

Runs on a worker thread, so it owns its own session.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from sqlmodel import Session

from app.ingest import thumbnails
from app.ingest.filetypes import TYPE_VIDEO
from app.ingest.probe import probe
from app.media_tools import ffmpeg_available
from app.models.asset import Asset
from app.services import assets as asset_service
from app.storage import build_storage, capacity, new_key, thumb_key_for

logger = logging.getLogger(__name__)

Progress = Callable[[str, int, str], None]

# The frame's short side, in pixels: "1080p" is 1920x1080 landscape and 1080x1920
# portrait alike. The short side rather than the height because Video Creator renders
# 9:16 and 1:1 as well as 16:9, and a phone's portrait 4K wants to become 1080x1920 for
# a 1080p vertical project, not 608x1080.
TARGETS = (2160, 1440, 1080, 720, 480, 360)

# How often a progress tick may write to the database. ffmpeg reports twice a second,
# and a row update per report is far more than the activity indicator can show.
_REPORT_EVERY_SECONDS = 2.0


class ReencodeError(Exception):
    """Something the user can be told about."""


def label_for(short_side: int) -> str:
    return "4K" if short_side == 2160 else f"{short_side}p"


def short_side_of(asset: Asset) -> Optional[int]:
    if not asset.width or not asset.height:
        return None
    return min(asset.width, asset.height)


def run(
    session: Session, asset: Asset, payload: Optional[str], progress: Progress
) -> tuple[str, str]:
    """Re-encode `asset` to the payload's `short_side`. Returns (detail, created_asset_id)."""
    try:
        data = json.loads(payload or "{}")
        short_side = int(data["short_side"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ReencodeError("Missing or invalid target resolution") from exc
    if short_side not in TARGETS:
        raise ReencodeError("Unsupported target resolution")
    if asset.asset_type != TYPE_VIDEO or not asset.storage_key:
        raise ReencodeError("The source file is not available")
    # Again, although the router already did: the job may have waited in the queue.
    if not capacity.has_room():
        raise ReencodeError(capacity.DISK_FULL_MESSAGE)

    storage = build_storage()
    progress("Encoding", 1, "")

    with storage.materialise(asset.storage_key) as source_path:
        with tempfile.TemporaryDirectory(prefix="gam-reencode-") as workdir:
            target_path = Path(workdir) / "reencode.mp4"
            encode(source_path, target_path, short_side, asset.duration_seconds, progress)

            progress("Reading metadata", 92, "")
            result = probe(target_path)
            preview = thumbnails.generate(
                target_path,
                TYPE_VIDEO,
                asset.original_name or "",
                duration_seconds=result.duration_seconds,
            )

            progress("Saving", 95, "")
            stored = storage.write_file(new_key(asset.user_id, ".mp4"), target_path)
            thumb_key = None
            if preview:
                thumb_key = storage.write_bytes(thumb_key_for(stored.key), preview).key

    label = label_for(short_side)
    created = asset_service.create_reencoded_asset(
        session,
        source_asset=asset,
        stored=stored,
        thumb_key=thumb_key,
        name=data.get("name") or f"{asset.name} ({label})",
        probe_result=result,
    )
    return f"{label} copy", created.id


def encode(
    source: Path,
    target: Path,
    short_side: int,
    duration_seconds: Optional[float],
    progress: Progress,
) -> None:
    """Scale `source` so its short side is `short_side`, into an H.264/AAC MP4."""
    if not ffmpeg_available():
        raise ReencodeError("ffmpeg is not available on this server")

    # Decided by ffmpeg from the frame it actually decodes, not from the probed
    # width/height: a phone video stores landscape pixels plus a rotation flag, ffmpeg
    # applies the rotation before the filter runs, and the probe can report either
    # orientation. -2 keeps the aspect ratio and rounds to the even size H.264 needs.
    #
    # setsar=1 because that rounding changes the shape very slightly, and `scale` makes
    # up for it with non-square pixels: a 1002x750 source became 480x360 pixels shown
    # 481 wide. Video Creator, like most editors, assumes square pixels; a 0.2% squash
    # nobody can see is the better trade.
    n = short_side
    scale = f"scale=w='if(gte(iw,ih),-2,{n})':h='if(gte(iw,ih),{n},-2)',setsar=1"
    argv = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-vf", scale,
        # The same codecs and preset as a sub-video's re-encode. CRF 20 is visually
        # indistinguishable from the source at the smaller size, which matters for a
        # copy that will be cut and re-rendered again downstream.
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        # Re-encoded rather than copied: an MP4 cannot hold every audio codec a source
        # might carry (Vorbis from a WebM, PCM from a camera), and audio costs nothing
        # next to the video.
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        "-y", str(target),
    ]
    stderr_path = target.with_suffix(".log")
    with stderr_path.open("w") as stderr_file:
        try:
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=stderr_file, text=True
            )
        except OSError as exc:
            raise ReencodeError(f"Could not run ffmpeg: {exc}") from exc

        try:
            assert proc.stdout is not None
            last_report = 0.0
            for line in proc.stdout:
                key, _, value = line.strip().partition("=")
                if key != "out_time_us" or time.monotonic() - last_report < _REPORT_EVERY_SECONDS:
                    continue
                last_report = time.monotonic()
                done = _seconds(value)
                percent = int(done / duration_seconds * 90) if duration_seconds and done else 1
                # Raises JobCancelled once the job is stopped, which unwinds through
                # the `finally` below and kills ffmpeg with it.
                progress("Encoding", max(1, percent), "")
            returncode = proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    if returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        stderr = stderr_path.read_text(errors="replace").strip()
        logger.warning("Re-encode of %s failed: %s", source.name, stderr[-400:])
        raise ReencodeError("Could not re-encode this video")


def _seconds(out_time_us: str) -> float:
    # "N/A" until the first frame is out.
    try:
        return max(0.0, int(out_time_us) / 1_000_000)
    except ValueError:
        return 0.0
