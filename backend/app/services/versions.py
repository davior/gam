"""A video's standard-resolution versions: listing, saving, deleting, and swapping one in.

`make_original` is how a library frees space without losing anything. A 4K original can
be replaced by its own 1080p version: the version's file becomes the asset's file, and
the big one is deleted. Same row, same id, so tags, attribution, transcript, clips and
every link to the asset carry on as they were. The timeline does not change with the
size, so a clip's in and out points and the transcript's timestamps stay true.
"""

from __future__ import annotations

import logging
import mimetypes
from typing import Optional

from sqlmodel import Session, col, select

from app.clock import utcnow
from app.ingest.filetypes import extension_of
from app.ingest.probe import ProbeResult
from app.models.asset import Asset
from app.models.version import AssetVersion
from app.schemas_versions import VersionRead
from app.services import assets as asset_service
from app.storage import LocalStorage, StoredFile, peaks_key_for

logger = logging.getLogger(__name__)


def find(session: Session, asset_id: str, short_side: int) -> Optional[AssetVersion]:
    return session.exec(
        select(AssetVersion).where(
            AssetVersion.asset_id == asset_id, AssetVersion.short_side == short_side
        )
    ).first()


def list_for(session: Session, asset_id: str) -> list[AssetVersion]:
    """Largest first, the order a size picker reads in."""
    return list(
        session.exec(
            select(AssetVersion)
            .where(AssetVersion.asset_id == asset_id)
            .order_by(col(AssetVersion.short_side).desc())
        ).all()
    )


def create(
    session: Session,
    *,
    asset: Asset,
    short_side: int,
    stored: StoredFile,
    probe_result: ProbeResult,
) -> AssetVersion:
    version = AssetVersion(
        asset_id=asset.id,
        user_id=asset.user_id,
        short_side=short_side,
        storage_key=stored.key,
        mime_type=mimetypes.guess_type(stored.key)[0],
        size_bytes=stored.size_bytes,
        checksum_sha256=stored.sha256,
        width=probe_result.width,
        height=probe_result.height,
        codec=probe_result.codec,
    )
    session.add(version)
    session.commit()
    session.refresh(version)
    return version


def delete(session: Session, storage: LocalStorage, version: AssetVersion) -> None:
    """Row first, then the file, for the reason `assets.delete_asset` gives."""
    key = version.storage_key
    session.delete(version)
    session.commit()
    storage.delete(key)
    storage.delete(peaks_key_for(key))


def make_original(
    session: Session, storage: LocalStorage, asset: Asset, version: AssetVersion
) -> Asset:
    """Swap `version`'s file in as `asset`'s own, and delete the file it replaces.

    The poster is kept rather than regenerated: it is a picture of the same frame, and
    `thumb_key` is a column of its own, so it does not go with the old file. The other
    versions are kept too, a larger one included: each was made from the original, so
    each is still a true copy of this video.
    """
    old_key = asset.storage_key
    asset.storage_key = version.storage_key
    asset.mime_type = version.mime_type
    asset.file_format = extension_of(version.storage_key).lstrip(".")
    asset.size_bytes = version.size_bytes
    asset.checksum_sha256 = version.checksum_sha256
    asset.width = version.width
    asset.height = version.height
    asset.codec = version.codec
    asset.metadata_modified_date = utcnow()
    session.add(asset)
    session.delete(version)
    session.commit()
    session.refresh(asset)

    # After the commit, as in `delete_asset`: a row pointing at a deleted file is the
    # broken state; a file nothing points at is what the stray sweep is for.
    if old_key:
        try:
            referenced = asset_service.still_referenced(session, asset.user_id, old_key)
        except Exception:  # noqa: BLE001 - keep the file when unsure
            logger.warning("Could not check whether %s is still in use", old_key, exc_info=True)
            referenced = True
        if not referenced:
            storage.delete(old_key)
            storage.delete(peaks_key_for(old_key))
    return asset


def to_read_model(version: AssetVersion) -> VersionRead:
    return VersionRead(
        id=version.id,
        asset_id=version.asset_id,
        short_side=version.short_side,
        width=version.width,
        height=version.height,
        size_bytes=version.size_bytes,
        codec=version.codec,
        mime_type=version.mime_type,
        file_url=asset_service.signed_url(version.storage_key),
        created_at=version.created_at,
    )
