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

from sqlmodel import Session, col, delete, select, update

from app.auth import sign_media_key
from app.clock import utcnow
from app.config import settings
from app.ingest import thumbnails
from app.ingest.filetypes import (
    SOURCE_CLIP,
    SOURCE_SUBVIDEO,
    SOURCE_UPLOAD,
    TYPE_AUDIO,
    TYPE_IMAGE,
    TYPE_VIDEO,
    asset_type_for,
    display_name_from,
    extension_of,
    sanitize_original_name,
)
from app.ingest.probe import ProbeResult, probe
from app.models.asset import Asset
from app.models.suggestion import Suggestion
from app.models.document import DocumentPage
from app.models.transcript import TranscriptSegment
from app.schemas_assets import AssetRead, AssetTagRead
from app.search import fts, vectors
from app.services import tags
from app.storage import LocalStorage, StorageError, StoredFile, new_key, thumb_key_for

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


def blocking_clips(session: Session, asset_id: str) -> list[Asset]:
    """Live clips that would be orphaned by deleting this asset.

    Only rows with no `storage_key` of their own count — a promoted clip or a
    freshly-extracted sub-video owns real bytes and does not depend on this asset
    still existing, even though it keeps `parent_asset_id` as a provenance breadcrumb.
    Called by the router *before* `delete_asset`, so a blocked delete never reaches the
    point of touching a row.
    """
    return list(
        session.exec(
            select(Asset).where(
                col(Asset.parent_asset_id) == asset_id, col(Asset.storage_key).is_(None)
            )
        ).all()
    )


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
    one used to be. `parent_asset_id` (M7) is the same category for a promoted clip or
    a fresh extraction that kept this asset as a provenance breadcrumb — the caller is
    expected to have already called `blocking_clips` and refused the delete if any
    *live* clip depends on this asset, but a survivor's mere breadcrumb still has to be
    cleared, or the same foreign key raises on it the moment this row is gone.
    """
    storage_key, thumb_key = asset.storage_key, asset.thumb_key

    asset_id, user_id = asset.id, asset.user_id

    tags.detach_all_from_asset(session, asset_id)
    session.exec(delete(Suggestion).where(col(Suggestion.asset_id) == asset_id))
    session.exec(delete(TranscriptSegment).where(col(TranscriptSegment.asset_id) == asset_id))
    session.exec(delete(DocumentPage).where(col(DocumentPage.asset_id) == asset_id))
    session.exec(update(Asset).where(col(Asset.parent_asset_id) == asset_id).values(parent_asset_id=None))
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


# ─── clips and sub-videos (M7) ────────────────────────────────────────────────


def _format_timestamp(seconds: float) -> str:
    total = max(int(seconds), 0)
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


def _format_range(start: float, end: float) -> str:
    return f"{_format_timestamp(start)}–{_format_timestamp(end)}"


def create_clip(
    session: Session,
    parent: Asset,
    *,
    in_point: float,
    out_point: float,
    name: Optional[str] = None,
) -> Asset:
    """A non-destructive clip: an Asset row, no file, no ffmpeg, no job.

    Validated against `parent` rather than trusted from the caller — `in_point`/
    `out_point` are user input by way of an in/out editor — and raises `ValueError`
    on anything that should not become a row, which the router turns into a 400, the
    same pattern `list_assets` already uses for `min_duration > max_duration`.
    Clipping a clip is refused rather than resolved to the real ancestor: `promote`
    already turns a clip into a real file in place, which covers "I want this exact
    range as a standalone file" without a second, coordinate-flattening code path.
    """
    if parent.asset_type not in (TYPE_VIDEO, TYPE_AUDIO):
        raise ValueError("Only video and audio assets can be clipped")
    if parent.storage_key is None:
        raise ValueError("A clip cannot itself be clipped — clip the original asset")
    if in_point < 0 or out_point <= in_point:
        raise ValueError("The out point must be after the in point")
    if parent.duration_seconds is not None and out_point > parent.duration_seconds:
        raise ValueError("The out point is past the end of the asset")

    clip = Asset(
        user_id=parent.user_id,
        name=name or f"Clip of {parent.name} ({_format_range(in_point, out_point)})",
        asset_type=parent.asset_type,
        source=SOURCE_CLIP,
        storage_key=None,
        # Copied, not resolved later: a clip's thumbnail is a real, already-existing
        # object, so it signs and serves exactly like any other asset's `thumb_key` —
        # only `file_url` needs `to_read_model`'s parent-resolution, since only
        # `storage_key` has to stay null for this to be a clip at all.
        thumb_key=parent.thumb_key,
        parent_asset_id=parent.id,
        in_point=in_point,
        out_point=out_point,
        mime_type=parent.mime_type,
        file_format=parent.file_format,
        duration_seconds=out_point - in_point,
        width=parent.width,
        height=parent.height,
        codec=parent.codec,
    )
    session.add(clip)
    session.commit()
    session.refresh(clip)
    _reindex(session, clip)
    return clip


def list_children(session: Session, parent_id: str, user_id: str) -> list[Asset]:
    """Everything derived from this asset: live clips and past promotions/extractions.

    Unfiltered by `storage_key` deliberately — a "Clips" tab wants the whole history,
    and the delete guard's promote UI filters this down to `source == SOURCE_CLIP`
    itself, so one endpoint serves both rather than needing a second for the narrower
    question `blocking_clips` already answers for the guard itself.
    """
    return list(
        session.exec(
            select(Asset)
            .where(col(Asset.parent_asset_id) == parent_id, Asset.user_id == user_id)
            .order_by(col(Asset.upload_date).desc())
        ).all()
    )


def create_subvideo_asset(
    session: Session,
    *,
    source_asset: Asset,
    stored: StoredFile,
    thumb_key: Optional[str],
    in_point: float,
    out_point: float,
    name: Optional[str],
    probe_result: ProbeResult,
) -> Asset:
    """A freshly-extracted sub-video: a brand-new, standalone Asset.

    `parent_asset_id` is provenance only — this row owns real bytes of its own
    (`storage_key=stored.key`), so it never blocks `source_asset`'s deletion and never
    appears in `blocking_clips`. `in_point`/`out_point` stay null: this file's own
    timeline starts at 0, unlike a clip's window into its parent's.
    """
    subvideo = Asset(
        user_id=source_asset.user_id,
        name=name or f"{source_asset.name} ({_format_range(in_point, out_point)})",
        asset_type=source_asset.asset_type,
        source=SOURCE_SUBVIDEO,
        storage_key=stored.key,
        thumb_key=thumb_key,
        parent_asset_id=source_asset.id,
        original_name=source_asset.original_name,
        mime_type=mimetypes.guess_type(stored.key)[0],
        file_format=extension_of(stored.key).lstrip("."),
        size_bytes=stored.size_bytes,
        checksum_sha256=stored.sha256,
        duration_seconds=probe_result.duration_seconds or (out_point - in_point),
        width=probe_result.width,
        height=probe_result.height,
        codec=probe_result.codec,
    )
    session.add(subvideo)
    session.commit()
    session.refresh(subvideo)
    _reindex(session, subvideo)
    return subvideo


def promote_clip(
    session: Session, clip: Asset, *, stored: StoredFile, thumb_key: Optional[str], probe_result: ProbeResult
) -> Asset:
    """Turn a live clip into a standalone sub-video, in place.

    Same row, same id — anything that already references this clip (a search hit, a
    link, a "Clips" tab entry) keeps pointing at something real. `parent_asset_id`
    stays, now as provenance rather than a live dependency. `in_point`/`out_point` are
    cleared, not kept: they were coordinates into the *parent's* timeline, and the
    extracted file has its own, starting at 0 — leaving the old values in place would
    have playback seek into a nine-second file at second 61.
    """
    clip.storage_key = stored.key
    clip.thumb_key = thumb_key or clip.thumb_key
    clip.source = SOURCE_SUBVIDEO
    clip.in_point = None
    clip.out_point = None
    clip.mime_type = mimetypes.guess_type(stored.key)[0]
    clip.file_format = extension_of(stored.key).lstrip(".")
    clip.size_bytes = stored.size_bytes
    clip.checksum_sha256 = stored.sha256
    if probe_result.duration_seconds is not None:
        clip.duration_seconds = probe_result.duration_seconds
    if probe_result.width is not None:
        clip.width = probe_result.width
    if probe_result.height is not None:
        clip.height = probe_result.height
    if probe_result.codec is not None:
        clip.codec = probe_result.codec
    clip.metadata_modified_date = utcnow()

    session.add(clip)
    session.commit()
    session.refresh(clip)
    _reindex(session, clip)
    return clip


# ─── serialisation ───────────────────────────────────────────────────────────


def parents_for_many(session: Session, assets: Iterable[Asset]) -> dict[str, Asset]:
    """Every listed asset's parent, in one query, for `to_read_model`'s `parent` arg.

    Same shape as `tags.tags_for_many`, and for the same reason: a listing's clips must
    not each ask for their own parent, which is a query per clip on the hottest page in
    the application. Most rows on a page are not clips at all, so this is usually a
    query over an empty set of ids — cheap, and simpler than special-casing that away.
    """
    parent_ids = {a.parent_asset_id for a in assets if a.parent_asset_id}
    if not parent_ids:
        return {}
    rows = session.exec(select(Asset).where(col(Asset.id).in_(parent_ids))).all()
    return {row.id: row for row in rows}


def to_read_model(
    asset: Asset,
    storage: LocalStorage,
    asset_tags: Optional[list] = None,
    parent: Optional[Asset] = None,
) -> AssetRead:
    """An Asset as the API returns it, with freshly signed URLs.

    `asset_tags` is passed in rather than fetched, for the same reason `parent` is:
    a listing loads every row's tags (and every clip's parent) in one query each and
    hands each row its slice, rather than asking per row on the hottest path in the
    application.

    A clip (`asset.storage_key is None`) has no file of its own — `file_url` and
    `missing` resolve against `parent`'s bytes instead, since that is what actually
    plays. `thumb_url` needs no such resolution: a clip's `thumb_key` is copied from
    its parent at creation time (`create_clip`), so it already points at a real object
    and signs like any other asset's.
    """
    playable_key = asset.storage_key if asset.storage_key else (parent.storage_key if parent else None)
    file_url = _signed_url(playable_key)
    thumb_url = _signed_url(asset.thumb_key)

    missing = False
    if playable_key:
        missing = not storage.stat(playable_key).exists

    return AssetRead(
        id=asset.id,
        name=asset.name,
        description=asset.description,
        summary=asset.summary,
        asset_type=asset.asset_type,
        source=asset.source,
        parent_asset_id=asset.parent_asset_id,
        in_point=asset.in_point,
        out_point=asset.out_point,
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
