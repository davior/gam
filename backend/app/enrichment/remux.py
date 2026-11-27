"""The remux job: a recording's own streams, rewritten into a container that knows its length.

A browser writes a recording as a live stream (MediaRecorder: Gecko Notes' camera and
screen captures). The container has no duration and no seek index, because neither is
known when the first byte goes out. Players then show nothing or a wrong guess, a seek
bar cannot seek, and a renderer cannot tell a 30-minute clip from a tenth of a second.
`probe()` now measures such a file from its packets, which fixes what is stored from
here on; this repairs the files already in a library, and on request.

`-c copy`: nothing is decoded or re-encoded, so it is quick, loses nothing, and keeps
every timestamp, which is what keeps a transcript's words and a clip's in and out points
where they were. The asset keeps its id, so tags, clips, playlists and every project
that names it carry on unchanged. Only the file behind it is replaced.

The order is the safety. The new file is written and then *checked* (same streams, same
length as the original measured from its packets) before anything points at it; the row is
switched in one commit; the old file goes last, as in `delete_asset`. Anything that goes
wrong before the commit leaves the original untouched.

Runs on a worker thread, so it owns its own session.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from sqlmodel import Session

from app.clock import utcnow
from app.enrichment.versions import _REPORT_EVERY_SECONDS
from app.ingest.filetypes import TYPE_AUDIO, TYPE_VIDEO, extension_of
from app.ingest.probe import ProbeResult, probe
from app.media_tools import ffmpeg_available
from app.models.asset import Asset
from app.services import assets as asset_service
from app.storage import build_storage, capacity, new_key, peaks_key_for

logger = logging.getLogger(__name__)

Progress = Callable[[str, int, str], None]

# A copied file differs from its source by container overhead and a frame at most; this
# is generous enough for that and far inside what a wrong length would be.
LENGTH_TOLERANCE_SECONDS = 1.5
LENGTH_TOLERANCE_FRACTION = 0.02


class RemuxError(Exception):
    """Something the user can be told about."""


def run(session: Session, asset: Asset, progress: Progress) -> str:
    """Rewrite `asset`'s file with a duration and an index. Returns the job's detail."""
    if asset.asset_type not in (TYPE_VIDEO, TYPE_AUDIO) or not asset.storage_key:
        raise RemuxError("This asset has no recording to repair")
    if not ffmpeg_available():
        raise RemuxError("ffmpeg is not available on this server")
    # Again, although the router already did: the job may have waited in the queue.
    if not capacity.has_room():
        raise RemuxError(capacity.DISK_FULL_MESSAGE)

    storage = build_storage()
    old_key = asset.storage_key
    extension = extension_of(old_key)
    progress("Repairing", 1, "")

    with storage.materialise(old_key) as source_path:
        before = probe(source_path)
        with tempfile.TemporaryDirectory(prefix="gam-remux-") as workdir:
            target_path = Path(workdir) / f"remuxed{extension}"
            copy_streams(source_path, target_path, asset.size_bytes, progress)

            progress("Checking", 92, "")
            after = probe(target_path)
            check(before, after)
            stored = storage.write_file(new_key(asset.user_id, extension), target_path)

    progress("Saving", 97, "")
    try:
        asset.storage_key = stored.key
        asset.size_bytes = stored.size_bytes
        asset.checksum_sha256 = stored.sha256
        asset.duration_seconds = after.duration_seconds
        asset.width = after.width or asset.width
        asset.height = after.height or asset.height
        asset.codec = after.codec or asset.codec
        asset.metadata_modified_date = utcnow()
        session.add(asset)
        session.commit()
    except Exception:
        # Nothing points at the new file, so it must not outlive the failure.
        session.rollback()
        storage.delete(stored.key)
        raise

    # After the commit: a row pointing at a deleted file is the broken state; a file
    # nothing points at is what the stray sweep is for.
    try:
        referenced = asset_service.still_referenced(session, asset.user_id, old_key)
    except Exception:  # noqa: BLE001 - keep the file when unsure
        logger.warning("Could not check whether %s is still in use", old_key, exc_info=True)
        referenced = True
    if not referenced:
        storage.delete(old_key)
        storage.delete(peaks_key_for(old_key))

    return f"{_clock(after.duration_seconds)} long"


def copy_streams(
    source: Path, target: Path, source_bytes: Optional[int], progress: Progress
) -> None:
    """Every video and audio stream of `source`, unchanged, into a fresh container."""
    argv = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        # Video and audio only, and either may be absent. `-map 0` would also carry data
        # and subtitle streams that a WebM cannot hold, failing the whole copy for them.
        "-map", "0:v?", "-map", "0:a?",
        "-c", "copy",
    ]
    if target.suffix.lower() in (".mp4", ".m4v", ".mov"):
        argv += ["-movflags", "+faststart"]
    argv += ["-progress", "pipe:1", "-nostats", "-y", str(target)]

    stderr_path = target.with_suffix(".log")
    with stderr_path.open("w") as stderr_file:
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=stderr_file, text=True)
        except OSError as exc:
            raise RemuxError(f"Could not run ffmpeg: {exc}") from exc

        try:
            assert proc.stdout is not None
            last_report = 0.0
            for line in proc.stdout:
                key, _, value = line.strip().partition("=")
                if key != "total_size" or time.monotonic() - last_report < _REPORT_EVERY_SECONDS:
                    continue
                last_report = time.monotonic()
                # By bytes, since the length of the file is the one thing not known.
                # Raises JobCancelled once the job is stopped, which unwinds through the
                # `finally` below and kills ffmpeg with it.
                progress("Repairing", _percent(value, source_bytes), "")
            returncode = proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    if returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        said = stderr_path.read_text(errors="replace").strip()
        logger.warning("Remuxing %s failed: %s", source.name, said[-400:])
        raise RemuxError("Could not rewrite this file; it is unchanged")


def check(before: ProbeResult, after: ProbeResult) -> None:
    """Refuse a copy that lost a stream, or came out a different length."""
    if (before.has_video, before.has_audio) != (after.has_video, after.has_audio):
        raise RemuxError("The rewritten copy lost a track, so the original was kept")
    if after.duration_seconds is None:
        raise RemuxError("The rewritten copy still has no length, so the original was kept")
    if before.duration_seconds is not None:
        gap = abs(after.duration_seconds - before.duration_seconds)
        if gap > max(LENGTH_TOLERANCE_SECONDS, LENGTH_TOLERANCE_FRACTION * before.duration_seconds):
            raise RemuxError(
                "The rewritten copy came out a different length from the original, "
                "so the original was kept"
            )


def _percent(total_size: str, source_bytes: Optional[int]) -> int:
    try:
        written = int(total_size)
    except ValueError:
        return 1    # "N/A" until the first packet is out
    if not source_bytes:
        return 1
    return max(1, min(90, int(written / source_bytes * 90)))


def _clock(seconds: Optional[float]) -> str:
    whole = int(round(seconds or 0))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
