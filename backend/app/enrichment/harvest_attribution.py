"""Re-reading embedded attribution across a whole library.

Ingest harvests every new upload, so anything added from M10 onward takes care of
itself. This is for everything added before — which, on an instance that has been
running a while, is the entire library. Those files still carry their EXIF and ID3 and
PDF Author on disk; nothing has ever looked.

One job walks the lot, for the same reasons `backfill.py` gives: one cancellable row,
one progress bar, one line in the activity feed rather than a wall of near-identical
ones.

Safe to run repeatedly. It only ever fills fields that are blank, so a second run is a
no-op over everything the first one filled and over everything the user has since
corrected by hand — there is no "already harvested" flag to keep, and no way for this to
walk back over an answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from sqlmodel import Session, col, select

from app.ingest import embedded_metadata
from app.ingest.probe import probe
from app.models.asset import Asset
from app.services import assets as asset_service
from app.storage import StorageError, build_storage

logger = logging.getLogger(__name__)

Progress = Callable[..., None]


@dataclass
class HarvestResult:
    scanned: int
    attributed: int
    failed: int


def run(session: Session, user_id: str, progress: Progress) -> HarvestResult:
    """Harvest every asset of this user's that owns a file."""
    storage = build_storage()

    # Clips are excluded by `storage_key IS NOT NULL`: a clip owns no bytes, and it
    # inherits its parent's attribution on read anyway, so there is nothing here for it.
    pending = list(
        session.exec(
            select(Asset)
            .where(Asset.user_id == user_id, col(Asset.storage_key).is_not(None))
            .order_by(col(Asset.upload_date).desc())
        ).all()
    )
    total = len(pending)

    scanned = attributed = failed = 0

    for index, asset in enumerate(pending):
        pct = int(index * 100 / total) if total else 100
        progress("Reading file metadata", pct, f"{index + 1} of {total} · {asset.name}")

        try:
            found = _harvest_one(storage, asset)
        except StorageError:
            # The file went missing underneath the row. Not this job's problem to fix,
            # and not a reason to stop.
            failed += 1
            continue
        except Exception:  # noqa: BLE001 - one unreadable file must not end the run
            failed += 1
            logger.warning("Could not harvest attribution for %s", asset.id, exc_info=True)
            continue

        scanned += 1
        if not found:
            continue

        before = {name: getattr(asset, name, None) for name in found}
        asset_service.apply_embedded_attribution(session, asset, found)
        session.refresh(asset)
        if any(getattr(asset, name, None) != before[name] for name in found):
            attributed += 1

    return HarvestResult(scanned=scanned, attributed=attributed, failed=failed)


def _harvest_one(storage, asset: Asset) -> dict:
    if not asset.storage_key:
        return {}

    with storage.materialise(asset.storage_key) as path:
        tags = probe(path).tags if asset.asset_type in ("video", "audio") else {}
        return embedded_metadata.harvest(
            path, asset.asset_type, asset.original_name or "", probe_tags=tags
        )
