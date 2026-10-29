"""What the library takes up on disk, and the files nothing refers to any more.

A user's breakdown comes from the database rather than a walk: `size_bytes` is recorded
as the bytes are written and files are write-once, so the column is exact and the query
is one indexed GROUP BY. Only thumbnails are counted on disk, because their sizes are
recorded nowhere — and a poster is shared between a parent and its clips, so counting
per row would count one file several times.

The server-wide numbers walk the media tree. That is a `stat` per file, which for a
library of tens of thousands of files is well under a second, and it runs only when an
admin opens Settings — not worth a cache that could disagree with the disk.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import timedelta

from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from app.config import settings
from app.models.asset import Asset
from app.schemas_storage import (
    CleanupResult,
    FileGroup,
    LargestAsset,
    LibraryUsage,
    ServerUsage,
    TypeUsage,
    VolumeRead,
)
from app.storage import LocalStorage, StorageError, peaks_key_for, thumb_key_for, validate_key
from app.storage import capacity

logger = logging.getLogger(__name__)

THUMB_SUFFIX = ".thumb.jpg"
PEAKS_SUFFIX = ".peaks.json"
LARGEST_LIMIT = 10

# A file is only a stray once it is this old. Every write path puts the bytes on disk
# *before* it commits the row that points at them — an upload, an import, an extraction
# and a generation alike — so a file nothing refers to yet may simply be one whose row
# is a moment away. No write takes a day. A sweep that is wrong anyway is recoverable:
# the backup sidecar snapshots `data/media` hourly.
STRAY_MIN_AGE = timedelta(hours=24)


# ─── one user ────────────────────────────────────────────────────────────────


def library_usage(session: Session, storage: LocalStorage, user_id: str) -> LibraryUsage:
    owns_file = (Asset.user_id == user_id, col(Asset.storage_key).is_not(None))

    rows = session.exec(
        select(Asset.asset_type, func.count(), func.coalesce(func.sum(Asset.size_bytes), 0))
        .where(*owns_file)
        .group_by(Asset.asset_type)
    ).all()
    by_type = sorted(
        (TypeUsage(asset_type=kind, files=count, bytes=total) for kind, count, total in rows),
        key=lambda row: row.bytes,
        reverse=True,
    )

    thumbnails = FileGroup()
    for key, stat in storage.files(user_id):
        # Cached waveforms count here too: like posters they are derived, recorded
        # nowhere, and shared between a parent and its clips.
        if key.endswith((THUMB_SUFFIX, PEAKS_SUFFIX)):
            thumbnails.files += 1
            thumbnails.bytes += stat.st_size

    largest = [
        LargestAsset(id=asset_id, name=name, asset_type=kind, size_bytes=size)
        for asset_id, name, kind, size in session.exec(
            select(Asset.id, Asset.name, Asset.asset_type, Asset.size_bytes)
            .where(*owns_file)
            .order_by(col(Asset.size_bytes).desc(), col(Asset.id))
            .limit(LARGEST_LIMIT)
        ).all()
    ]

    originals = sum(row.bytes for row in by_type)
    return LibraryUsage(
        total_bytes=originals + thumbnails.bytes,
        files=sum(row.files for row in by_type),
        by_type=by_type,
        thumbnails=thumbnails,
        largest=largest,
    )


# ─── the whole server ────────────────────────────────────────────────────────


def level(volumes: list[capacity.Volume]) -> str:
    tightest = capacity.tightest(volumes)
    return capacity.level_for(tightest.free_bytes) if tightest else capacity.LEVEL_OK


def server_usage(
    session: Session, storage: LocalStorage, volumes: list[capacity.Volume]
) -> ServerUsage:
    media = FileGroup()
    stray = FileGroup()
    for _key, size in _scan(session, storage, media):
        stray.files += 1
        stray.bytes += size

    return ServerUsage(
        volumes=[
            VolumeRead(
                roles=volume.roles,
                total_bytes=volume.total_bytes,
                used_bytes=volume.used_bytes,
                free_bytes=volume.free_bytes,
                level=capacity.level_for(volume.free_bytes),
                guarded=volume.guarded,
            )
            for volume in volumes
        ],
        media=media,
        database_bytes=_database_bytes(),
        stray=stray,
        warn_free_bytes=int(settings.disk_warn_free_gb * capacity.GB),
        reserve_bytes=int(settings.disk_reserve_gb * capacity.GB),
    )


def remove_strays(session: Session, storage: LocalStorage) -> CleanupResult:
    """Delete every stray, found afresh rather than from what an earlier page showed.

    Through `storage.delete`, which validates the key and resolves it inside the root,
    so a sweep cannot reach anything a normal delete could not.
    """
    removed = FileGroup()
    skipped = 0
    for key, size in list(_scan(session, storage)):
        if storage.delete(key):
            removed.files += 1
            removed.bytes += size
        else:
            skipped += 1
    if removed.files:
        logger.info("Removed %d stray files (%d bytes)", removed.files, removed.bytes)
    return CleanupResult(removed=removed, skipped=skipped)


def _scan(session: Session, storage: LocalStorage, totals: FileGroup | None = None):
    """Yield `(key, size)` for each stray, adding every file seen to `totals`.

    A stray is a file old enough (`STRAY_MIN_AGE`) that no row's `storage_key` or
    `thumb_key` names — left by a delete whose unlink failed, or by an upload or job
    that died between writing the bytes and committing the row. Interrupted writes'
    `.partial` files are strays by the same rule: no row ever names one.

    A name that is not a valid key is skipped rather than offered: GAM never writes
    one, so it is somebody else's file, and `storage.delete` would refuse it anyway.
    """
    referenced = _referenced_keys(session)
    cutoff = time.time() - STRAY_MIN_AGE.total_seconds()

    for key, stat in storage.files():
        if totals is not None:
            totals.files += 1
            totals.bytes += stat.st_size
        if key in referenced or stat.st_mtime > cutoff:
            continue
        try:
            validate_key(key)
        except StorageError:
            continue
        yield key, stat.st_size


def _referenced_keys(session: Session) -> set[str]:
    """Every key any row points at, across all users.

    The derived thumbnail and waveform names are kept as well as the columns. A thumbnail write that
    succeeded under a row whose `thumb_key` update did not is still that asset's poster
    — `thumb_key_for` exists so one can always be found from the asset's own key — and
    deleting it would be unrecoverable where keeping it costs a few kilobytes.
    """
    keys: set[str] = set()
    rows = session.exec(
        select(Asset.storage_key, Asset.thumb_key).where(
            or_(col(Asset.storage_key).is_not(None), col(Asset.thumb_key).is_not(None))
        )
    ).all()
    for storage_key, thumb_key in rows:
        if storage_key:
            keys.add(storage_key)
            try:
                keys.add(thumb_key_for(storage_key))
                # Never in a column at all, so the derived name is the only way a sweep
                # can know a cached waveform is still its file's.
                keys.add(peaks_key_for(storage_key))
            except StorageError:
                pass
        if thumb_key:
            keys.add(thumb_key)
    return keys


def _database_bytes() -> int | None:
    """The SQLite file with its WAL and shared-memory sidecars.

    The WAL is not small change: between checkpoints it holds every committed write the
    main file has not absorbed yet, and after a bulk import it can rival the database.
    """
    path = capacity.database_path()
    if path is None:
        return None
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.stat(f"{path}{suffix}").st_size
        except OSError:
            continue
    return total
