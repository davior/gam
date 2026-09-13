"""Turning an upload into a catalogued asset, and reading one back out.

Kept out of the router so the pipeline can be tested without HTTP and reused by the
later milestones that create assets without an upload — an extracted sub-video (M7) and
an AI generation (M8) both land here.
"""

from __future__ import annotations

import json
import logging
import mimetypes
from typing import AsyncIterator, Iterable, Optional

from sqlmodel import Session

from app.auth import sign_media_key
from app.clock import utcnow
from app.config import settings
from app.ingest import thumbnails
from app.ingest.filetypes import (
    SOURCE_UPLOAD,
    TYPE_IMAGE,
    asset_type_for,
    display_name_from,
    extension_of,
    sanitize_original_name,
)
from app.ingest.probe import probe
from app.models.asset import Asset
from app.schemas_assets import AssetRead, AssetTagRead
from app.search import fts
from app.services import tags
from app.storage import LocalStorage, StorageError, new_key, thumb_key_for

logger = logging.getLogger(__name__)


class UnsupportedFile(Exception):
    """The file's extension is not in the allowlist."""

    code = "unsupported_type"


async def ingest_upload(
    session: Session,
    storage: LocalStorage,
    *,
    user_id: str,
    filename: str,
    chunks: AsyncIterator[bytes],
    content_type: Optional[str] = None,
    source: str = SOURCE_UPLOAD,
) -> Asset:
    """Store the bytes, then describe them.

    The order matters. The file is written and the row committed before any probing or
    thumbnailing, so an upload is durable the moment it lands — enrichment that fails
    or is slow leaves a poorer asset, never a lost one. This is FR 6.1.2 and 6.1.3:
    nothing blocks ingestion.
    """
    asset_type = asset_type_for(filename)
    if asset_type is None:
        raise UnsupportedFile(filename)

    extension = extension_of(filename)
    key = new_key(user_id, extension)
    stored = await storage.write_stream(key, chunks)

    original_name = sanitize_original_name(filename)
    asset = Asset(
        user_id=user_id,
        name=display_name_from(filename),
        asset_type=asset_type,
        source=source,
        storage_key=stored.key,
        original_name=original_name,
        mime_type=content_type or mimetypes.guess_type(filename)[0],
        file_format=extension.lstrip(".") or None,
        size_bytes=stored.size_bytes,
        checksum_sha256=stored.sha256,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)

    _reindex(session, asset)
    _describe(session, storage, asset)
    return asset


def _describe(session: Session, storage: LocalStorage, asset: Asset) -> None:
    """Probe and thumbnail an already-stored asset.

    Best-effort throughout: this runs after the row is committed, and a failure here
    must leave the asset intact rather than roll anything back.
    """
    if not asset.storage_key:
        return

    try:
        with storage.materialise(asset.storage_key) as path:
            result = probe(path)
            asset.width = result.width
            asset.height = result.height

            # ffprobe models a still image as a one-frame video stream, so a JPEG
            # comes back with codec "mjpeg" and a duration of one frame — 0.04s. Both
            # are artefacts of that model rather than facts about the picture, and
            # carrying them through would put a "0:00" badge on every image tile and
            # list a codec for a file that has none. probe() stays a faithful reporter
            # of what ffprobe said; deciding what is meaningful for an asset type
            # belongs here.
            if asset.asset_type != TYPE_IMAGE:
                asset.duration_seconds = result.duration_seconds
                asset.codec = result.codec

            preview = thumbnails.generate(
                path,
                asset.asset_type,
                asset.original_name or "",
                duration_seconds=result.duration_seconds,
            )
    except (StorageError, OSError) as exc:
        logger.warning("Could not describe asset %s: %s", asset.id, exc)
        return

    if preview:
        try:
            thumb = storage.write_bytes(thumb_key_for(asset.storage_key), preview)
            asset.thumb_key = thumb.key
        except (StorageError, OSError) as exc:
            logger.warning("Could not store thumbnail for %s: %s", asset.id, exc)

    session.add(asset)
    session.commit()
    session.refresh(asset)
    _reindex(session, asset)


def _reindex(session: Session, asset: Asset) -> None:
    """Keep the keyword index in step with the row.

    Tags are read here rather than passed in, and that is the point: this is the single
    function every write path already calls, so tagging, untagging, bulk apply and tag
    deletion all keep search correct by doing what they were going to do anyway. Passing
    `tags_text` from each caller would work exactly until one of them forgot, and the
    symptom — a tag that is attached but unsearchable — is invisible without looking.

    Never raises: a stale search index is a worse search result, while a failed upload
    is a lost file. The two are not remotely equal, so indexing does not get a vote on
    whether the write succeeded.
    """
    try:
        fts.index_asset(session, asset, tags_text=tags.tags_text_for(session, asset.id))
    except Exception:  # noqa: BLE001 - see above
        logger.warning("Could not index asset %s for search", asset.id, exc_info=True)


def reindex_ids(session: Session, asset_ids: Iterable[str]) -> None:
    """Re-index a set of assets after something changed their tags in bulk."""
    for asset_id in set(asset_ids):
        asset = session.get(Asset, asset_id)
        if asset is not None:
            _reindex(session, asset)


def delete_asset(session: Session, storage: LocalStorage, asset: Asset) -> None:
    """Remove the row and the bytes it owns.

    The row goes first. If the unlink fails the asset is still gone from the user's
    view, and an orphaned file is recoverable by a sweep; the reverse — a row pointing
    at nothing — is what produces broken images.
    """
    storage_key, thumb_key = asset.storage_key, asset.thumb_key

    asset_id = asset.id
    session.delete(asset)
    session.commit()

    try:
        fts.remove_asset(session, asset_id)
    except Exception:  # noqa: BLE001
        logger.warning("Could not un-index asset %s", asset_id, exc_info=True)

    for key in (storage_key, thumb_key):
        if key:
            storage.delete(key)


def apply_metadata(session: Session, asset: Asset, changes: dict) -> Asset:
    """Apply a user's manual edits, recording that a human made them.

    The provenance write is what FR 8.1.3 rests on: a later AI enrichment run reads it
    to know which fields a person has touched, and must not overwrite those without
    asking. Recording it here — at the only place manual edits happen — is what keeps
    that guarantee from depending on every future caller remembering it.
    """
    if not changes:
        return asset

    try:
        provenance = json.loads(asset.field_provenance or "{}")
    except ValueError:
        provenance = {}

    for field, value in changes.items():
        setattr(asset, field, value)
        provenance[field] = "human"

    asset.field_provenance = json.dumps(provenance, sort_keys=True)
    asset.metadata_modified_date = utcnow()

    session.add(asset)
    session.commit()
    session.refresh(asset)
    _reindex(session, asset)
    return asset


# ─── serialisation ───────────────────────────────────────────────────────────


def to_read_model(
    asset: Asset, storage: LocalStorage, asset_tags: Optional[list] = None
) -> AssetRead:
    """An Asset as the API returns it, with freshly signed URLs.

    `asset_tags` is passed in rather than fetched. A listing loads every row's tags in
    one query and hands each one its slice; fetching here instead would put a query per
    asset on the hottest path in the application.
    """
    file_url = _signed_url(asset.storage_key)
    thumb_url = _signed_url(asset.thumb_key)

    missing = False
    if asset.storage_key:
        missing = not storage.stat(asset.storage_key).exists

    return AssetRead(
        id=asset.id,
        name=asset.name,
        description=asset.description,
        summary=asset.summary,
        asset_type=asset.asset_type,
        source=asset.source,
        original_name=asset.original_name,
        mime_type=asset.mime_type,
        file_format=asset.file_format,
        size_bytes=asset.size_bytes,
        duration_seconds=asset.duration_seconds,
        width=asset.width,
        height=asset.height,
        codec=asset.codec,
        file_url=file_url,
        thumb_url=thumb_url,
        missing=missing,
        tags=[
            AssetTagRead(id=t.id, name=t.name, category_id=t.category_id)
            for t in (asset_tags or [])
        ],
        upload_date=asset.upload_date,
        modified_date=asset.modified_date,
        metadata_modified_date=asset.metadata_modified_date,
    )


def _signed_url(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    expires_at, signature = sign_media_key(key, ttl_seconds=settings.media_url_ttl_seconds)
    return f"/media/{key}?exp={expires_at}&sig={signature}"
