"""Whether the media tooling this app depends on is actually present.

Probed once at startup rather than discovered when a user's first upload fails. The
result is reported by /api/health, so a deployment can be checked before it is trusted
with anything.
"""

import logging
import shutil
import subprocess
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@lru_cache
def ffmpeg_version() -> str:
    if not ffmpeg_available():
        return ""
    try:
        out = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=10
        )
        return out.stdout.splitlines()[0] if out.stdout else ""
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ffmpeg present but would not report a version: %s", exc)
        return ""
