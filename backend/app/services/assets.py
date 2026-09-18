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

from sqlmodel import Session, col, delete

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
from app.models.suggestion import Suggestion
from app.models.document import DocumentPage
from app.models.transcript import TranscriptSegment
from app.schemas_assets import AssetRead, AssetTagRead
from app.search import fts, vectors
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
    _chain_transcription(session, asset)
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


def _chain_transcription(session: Session, asset: Asset) -> None:
    """Transcribe audio and video the moment they land, without being asked.

    Mirrors `jobs/enrichment.py::_chain_embedding`, one step earlier in the same
    chain: a video that nobody transcribes has nothing for describe, summarise,
    autotag or search to read, and asking a user to press Transcribe by hand before
    any of that works is a step the pipeline does not need them for.

    Guarded on a Deepgram key actually being configured, for the same reason the
    embedding chain is guarded on a provider: queueing unconditionally would put a
    red "no Deepgram key" row in the activity feed after every single upload, which
    trains people to ignore it.

    Imports `app.jobs.enrichment` lazily rather than at module load — that module's
    import chain leads back through `app.enrichment.describe` to this one, and
    importing it at the top of this file would be a cycle.
    """
    from app.enrichment.transcribe import can_transcribe
    from app.jobs import enrichment as enrichment_jobs
    from app.models.job import KIND_TRANSCRIBE
    from app.settings_store import load_deepgram_key

    if not can_transcribe(asset):
        return

    try:
        if not load_deepgram_key(session, asset.user_id):
            logger.info(
                "Not transcribing asset %s: no Deepgram key configured for this user",
                asset.id,
            )
            return

        if enrichment_jobs.active_job(session, asset.id, KIND_TRANSCRIBE) is not None:
            return

        enrichment_jobs.submit(session, asset, KIND_TRANSCRIBE)
    except Exception:  # noqa: BLE001 - an upload that succeeded must stay succeeded
        logger.warning("Could not queue transcription for asset %s", asset.id, exc_info=True)


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
    """Remove the row, everything that hangs off it, and the bytes it owns.

    The row goes first. If the unlink fails the asset is still gone from the user's
    view, and an orphaned file is recoverable by a sweep; the reverse — a row pointing
    at nothing — is what produces broken images.

    The dependent rows have to go before it, for two different reasons. Tag
    attachments are a hard constraint: `AssetTag.asset_id` is a foreign key with no
    `ON DELETE`, so with `PRAGMA foreign_keys=ON` SQLite refuses the delete outright
    and a tagged asset cannot be removed at all. Transcript segments and embeddings
    are not — they are plain indexed columns, so they would simply be orphaned, which
    is quieter and worse: a stale vector keeps matching a search, and the hit is then
    dropped when its asset cannot be loaded, so the library silently returns fewer
    results than it should with nothing logged.

    Suggestions are in the first category — `Suggestion.asset_id` is a foreign key too,
    so an asset with a pending suggestion would be undeletable exactly the way a tagged
    one used to be.
    """
    storage_key, thumb_key = asset.storage_key, asset.thumb_key

    asset_id, user_id = asset.id, asset.user_id

    tags.detach_all_from_asset(session, asset_id)
    session.exec(delete(Suggestion).where(col(Suggestion.asset_id) == asset_id))
    session.exec(delete(TranscriptSegment).where(col(TranscriptSegment.asset_id) == asset_id))
    session.exec(delete(DocumentPage).where(col(DocumentPage.asset_id) == asset_id))
    session.delete(asset)
    session.commit()

    # Both of these run after the commit, and neither gets to fail the delete: the row
    # is already gone, so raising here would abort the rest of the cleanup and leave
    # more behind than it removed. A stale index entry is recoverable; a half-finished
    # delete is the thing this function exists to avoid.
    try:
        # Drops its own transaction and invalidates the per-user vector cache, which
        # must not happen while the asset could still come back.
        vectors.remove_for_asset(session, user_id, asset_id)
    except Exception:  # noqa: BLE001 - see above
        logger.warning("Could not drop embeddings for asset %s", asset_id, exc_info=True)

    try:
        fts.remove_asset(session, asset_id)
    except Exception:  # noqa: BLE001
        logger.warning("Could not un-index asset %s", asset_id, exc_info=True)

    for key in (storage_key, thumb_key):
        if key:
            storage.delete(key)


def apply_metadata(session: Session, asset: Asset, changes: dict) -> Asset:
    """Apply a user's manual edits, recording that a human made them.

    Stamping "human" here still matters for `name`: a suggested title is only ever
    applied through this path (`services/suggestions.py::accept`), and there is no
    direct AI write path that could replace it afterwards. For `description` and
    `summary` the stamp is now informational only — `apply_ai_metadata` no longer reads
    it before overwriting either field.
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


def apply_ai_metadata(session: Session, asset: Asset, changes: dict) -> list[str]:
    """Write fields an enrichment job produced.

    The counterpart to `apply_metadata`. Pressing a "Generate"/"Summarize"/"Describe"
    control is a deliberate, explicit request for a fresh answer, so it always lands —
    including over a value a person typed by hand. `field_provenance` is still stamped
    "ai" afterwards, so the record of who wrote a field stays accurate even though
    nothing here gates on it any more.

    Returns the fields written (always every key in `changes`, once any are given).
    """
    if not changes:
        return []

    try:
        provenance = json.loads(asset.field_provenance or "{}")
    except ValueError:
        provenance = {}

    for field_name, value in changes.items():
        setattr(asset, field_name, value)
        provenance[field_name] = "ai"

    asset.field_provenance = json.dumps(provenance, sort_keys=True)
    asset.metadata_modified_date = utcnow()

    session.add(asset)
    session.commit()
    session.refresh(asset)
    _reindex(session, asset)
    return list(changes.keys())


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
