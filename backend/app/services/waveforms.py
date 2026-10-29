"""Serving a recording's waveform, computed on first view and cached beside the file.

Lazily, rather than at ingest: there is then no column, no migration, and no backfill
for the library that existed before this did. The first open of a long recording pays
for one ffmpeg decode, a few seconds even at ninety minutes; every open after reads a
small JSON file.

Keyed by the *file*, not the asset. A clip plays its parent's bytes, so it shares its
parent's waveform and draws its window over it, and a clip opened before its parent
fills the same cache the parent then reads.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import HTTPException, status
from sqlmodel import Session

from app.ingest import waveform
from app.ingest.filetypes import TYPE_AUDIO
from app.models.asset import Asset
from app.schemas_assets import WaveformRead
from app.storage import LocalStorage, StorageError, peaks_key_for

logger = logging.getLogger(__name__)


def waveform_for(session: Session, storage: LocalStorage, asset: Asset) -> WaveformRead:
    if asset.asset_type != TYPE_AUDIO:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "not_audio", "message": "Only audio has a waveform"},
        )

    key = _playable_key(session, asset)
    if not key or not storage.stat(key).exists:
        raise _unavailable("The file behind this asset is missing")

    cache_key = peaks_key_for(key)
    cached = _read_cache(storage, cache_key)
    if cached:
        return cached

    with storage.materialise(key) as path:
        computed = waveform.compute(path)
    if computed is None:
        raise _unavailable("A waveform could not be made from this file")

    result = WaveformRead(duration=computed.duration, peaks=computed.peaks)
    try:
        storage.write_bytes(cache_key, result.model_dump_json().encode())
    except (StorageError, OSError):
        # Served anyway. Not caching costs the next open another decode; refusing would
        # cost the player its waveform over a full disk that has nothing to do with it.
        logger.warning("Could not cache the waveform at %s", cache_key, exc_info=True)
    return result


def _playable_key(session: Session, asset: Asset) -> Optional[str]:
    """The same resolution `assets.to_read_model` makes for `file_url`."""
    if asset.storage_key:
        return asset.storage_key
    if asset.parent_asset_id:
        parent = session.get(Asset, asset.parent_asset_id)
        return parent.storage_key if parent else None
    return None


def _read_cache(storage: LocalStorage, key: str) -> Optional[WaveformRead]:
    """The cached waveform, or None to recompute.

    A cache file that does not parse is treated as absent rather than as an error: it is
    only ever a copy of something this module can make again, and an interrupted write
    is the likeliest way for one to be wrong.
    """
    if not storage.stat(key).exists:
        return None
    try:
        with storage.open(key) as handle:
            return WaveformRead.model_validate(json.load(handle))
    except (StorageError, OSError, ValueError):
        logger.warning("Ignoring an unreadable waveform cache at %s", key, exc_info=True)
        return None


def _unavailable(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "waveform_unavailable", "message": message},
    )
