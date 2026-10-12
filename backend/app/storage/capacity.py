"""How much room the server has left, and the guard that stops writes when it has none.

Read with `statvfs` on the directories GAM writes to. Inside Docker those are bind
mounts, so the answer is the *host* disk's — which is what anyone asking "how full is
the server" means, and the only number that predicts when a write will fail.

The guard exists because nothing else stops a write. A full disk fails an import
halfway, leaves `.partial` files behind, and can fail a SQLite commit mid-transaction;
refusing new work while there is still a reserve left is far cheaper than any of those.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.engine import make_url

from app.config import settings

logger = logging.getLogger(__name__)

ROLE_MEDIA = "media"
ROLE_DATABASE = "database"
# The container's temp directory. Uploads spool there before the handler runs, and
# imports, transcodes and generations download there before the copy into media — so a
# two-gigabyte import needs two gigabytes here first. Under Docker it is the container's
# own layer, commonly a different disk from the bind-mounted data.
ROLE_WORKING = "working"

LEVEL_OK = "ok"
LEVEL_LOW = "low"
LEVEL_CRITICAL = "critical"

GB = 1024**3

DISK_FULL_CODE = "disk_full"
DISK_FULL_MESSAGE = (
    "The server's disk is nearly full, so nothing new can be stored until space is "
    "freed. Settings → Storage shows what is using it."
)


@dataclass
class Volume:
    """One filesystem, and every role GAM puts on it."""

    roles: list[str] = field(default_factory=list)
    total_bytes: int = 0
    # Not `total - free`. ext4 reserves about 5% of blocks for root, and `free` here is
    # what a non-root process can still write, so `used + free` falls short of `total`
    # by that reserve. Computing free as the difference would promise space no write
    # can have; the UI shows the gap as "reserved" instead.
    used_bytes: int = 0
    free_bytes: int = 0

    @property
    def guarded(self) -> bool:
        """Whether filling this disk would damage anything that lasts.

        The working volume alone is not. A download that does not fit there fails with
        a plain error and its temp directory is removed; nothing persistent is touched.
        It is also, on a development machine, often a tmpfs sized from RAM, and holding
        it to a 5 GB reserve would refuse every upload on a laptop with 8 GB of memory.
        """
        return ROLE_MEDIA in self.roles or ROLE_DATABASE in self.roles


def _disk_usage(path: Path):
    """The seam tests patch: `shutil.disk_usage` cannot be made to report a full disk."""
    return shutil.disk_usage(path)


def _device(path: Path) -> int:
    return os.stat(path).st_dev


def database_path() -> Optional[Path]:
    """The SQLite file, or None when the database is not a local file."""
    url = make_url(settings.resolved_database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def _paths() -> list[tuple[str, Path]]:
    paths = [(ROLE_MEDIA, settings.resolved_media_dir)]
    database = database_path()
    if database is not None:
        paths.append((ROLE_DATABASE, database.parent))
    paths.append((ROLE_WORKING, Path(tempfile.gettempdir())))
    return paths


def volumes() -> list[Volume]:
    """Each distinct filesystem GAM writes to, media first.

    Merged by device, so the usual layout — `data/db` and `data/media` side by side —
    reads as one disk rather than as the same numbers twice. A path that cannot be read
    is left out and logged rather than raised: an unreadable statvfs must not turn the
    Settings page, or an upload, into a 500.
    """
    found: dict[int, Volume] = {}
    for role, path in _paths():
        try:
            device = _device(path)
            usage = _disk_usage(path)
        except OSError as exc:
            logger.warning("Could not read disk usage for %s (%s): %s", role, path, exc)
            continue
        if device in found:
            found[device].roles.append(role)
            continue
        found[device] = Volume(
            roles=[role],
            total_bytes=usage.total,
            used_bytes=usage.used,
            free_bytes=usage.free,
        )
    return list(found.values())


def level_for(free_bytes: int) -> str:
    if free_bytes < settings.disk_reserve_gb * GB:
        return LEVEL_CRITICAL
    if free_bytes < settings.disk_warn_free_gb * GB:
        return LEVEL_LOW
    return LEVEL_OK


def tightest(found: list[Volume]) -> Optional[Volume]:
    """The guarded volume with the least room — the one that decides the level."""
    guarded = [volume for volume in found if volume.guarded]
    return min(guarded, key=lambda volume: volume.free_bytes) if guarded else None


def has_room(needed_bytes: int = 0) -> bool:
    """Whether a write of `needed_bytes` would still leave the reserve free.

    The size counts against the media volume only: that is where the file lands, and
    the database's share of any write is a few rows. True when nothing could be read —
    the guard fails open, for the same reason `volumes()` does not raise.
    """
    reserve = settings.disk_reserve_gb * GB
    for volume in volumes():
        if not volume.guarded:
            continue
        needed = max(needed_bytes, 0) if ROLE_MEDIA in volume.roles else 0
        if volume.free_bytes - needed < reserve:
            return False
    return True


def disk_full_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
        detail={"code": DISK_FULL_CODE, "message": DISK_FULL_MESSAGE},
    )


def require_room(needed_bytes: int = 0) -> None:
    """Raise a 507 unless `has_room`. For routers, before they queue the work."""
    if not has_room(needed_bytes):
        raise disk_full_error()
