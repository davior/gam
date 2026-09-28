"""The import_url job: a link in, a catalogued asset out.

What the user asked for, and the decisions behind each step, are in docs/url-import.md.
In order:

1. Read the link. A playlist fans out into one job per video and stops there.
2. Refuse a live stream, and skip a video that is already in the library.
3. Download it — with the site's captions too, if there is no Deepgram key to
   transcribe it with instead.
4. Catalogue it through the same `ingest_file` path an upload takes, so it is probed,
   posterised, indexed, and queued for Deepgram exactly as an upload would be.
5. Write what the site said about it, the thumbnail the site shows, the uploader's tags,
   one clip per chapter, and the captions as a transcript.

Everything after step 4 is best-effort in the way the rest of ingest is: the video is
safely in the library by then, and a chapter that will not clip must not take it away.

Runs on a worker thread, so it owns its session through the job's, and reports progress
through the callback the queue hands it — which is also the cancellation checkpoint.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from fastapi import HTTPException
from sqlmodel import Session, col, select

from app.clock import utcnow
from app.enrichment.deepgram import Transcript
from app.enrichment.transcribe import store_transcript
from app.ingest import captions as caption_parser
from app.ingest import thumbnails, ytdlp
from app.ingest.filetypes import SOURCE_URL, TYPE_IMAGE
from app.ingest.ytdlp import ImportedMetadata, UrlImportError
from app.jobs.runner import readable_error, set_fields
from app.models.asset import Asset
from app.models.job import EnrichmentJob
from app.services import assets as asset_service
from app.services import suggestions as suggestion_service
from app.services import tags as tag_service
from app.settings_store import load_deepgram_key
from app.storage import StorageError, build_storage, thumb_key_for

logger = logging.getLogger(__name__)

Progress = Callable[[str, int, str], None]

# A playlist entry can itself be a playlist — a channel URL resolves to its tabs, and
# each tab to its videos. Two levels covers that; anything deeper is a structure nobody
# meant to import wholesale by pasting one link.
MAX_PLAYLIST_DEPTH = 2

# A chapter shorter than this is a table-of-contents artefact (a zero-length "Intro"
# marker, a one-second sponsor stub) rather than something anybody will want to play.
MIN_CHAPTER_SECONDS = 1.0


@dataclass(frozen=True)
class ImportRequest:
    """What one import job was asked to do. Carried in `EnrichmentJob.payload`."""

    url: str
    audio_only: bool = False
    apply_tags: bool = True
    chapters_as_clips: bool = True
    # How many playlists deep this job was queued from. Not user input.
    depth: int = 0

    def encode(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def decode(cls, payload: Optional[str]) -> "ImportRequest":
        try:
            data = json.loads(payload or "{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict) or not isinstance(data.get("url"), str):
            raise UrlImportError("This import job has no link to fetch")
        return cls(
            url=data["url"],
            audio_only=bool(data.get("audio_only", False)),
            apply_tags=bool(data.get("apply_tags", True)),
            chapters_as_clips=bool(data.get("chapters_as_clips", True)),
            depth=int(data.get("depth") or 0),
        )


@dataclass
class ImportResult:
    detail: str
    created_asset_id: Optional[str] = None
    # Set only when captions became the transcript, so the worker can chain the embed
    # job the way it does after a Deepgram run — otherwise the transcript is keyword-
    # searchable and invisible to semantic search.
    captioned_asset: Optional[Asset] = None


def run(session: Session, job: EnrichmentJob, progress: Progress) -> ImportResult:
    request = ImportRequest.decode(job.payload)

    try:
        # Again, although the router already did: a playlist's children arrive here with
        # URLs the site chose, not the user, and they get the same check.
        url = ytdlp.normalise_url(request.url)
    except HTTPException as exc:
        raise UrlImportError(readable_error(exc)) from exc

    with tempfile.TemporaryDirectory(prefix="gam-import-") as workdir:
        work = Path(workdir)

        progress("Reading the link", 2, "")
        info = ytdlp.probe_url(url, work)

        if ytdlp.is_playlist(info):
            return _fan_out(session, job, request, info)

        ytdlp.refuse_if_live(info)
        meta = ytdlp.metadata_from_info(info)

        existing = _already_imported(session, job.user_id, [meta.source_url or url])
        if existing:
            return ImportResult(
                detail="Already in your library", created_asset_id=existing[meta.source_url or url]
            )

        set_fields(session, job, asset_name=meta.title)

        # The user's rule: Deepgram when there is a key (the upload chain queues it on
        # its own), the site's captions when there is not. Asked once, here, so the
        # download knows whether to fetch them at all. A stored key that will not
        # decrypt counts as no key — the upload chain treats it the same way, so
        # captions are the only transcript this video would otherwise get.
        try:
            want_captions = not load_deepgram_key(session, job.user_id)
        except Exception:  # noqa: BLE001 - a bad setting must not cost the import
            logger.warning("Could not read the Deepgram key for %s", job.user_id, exc_info=True)
            want_captions = True

        downloaded = ytdlp.download(
            url,
            info,
            work,
            audio_only=request.audio_only,
            want_captions=want_captions,
            progress=progress,
        )

        progress("Saving", 90, "")
        storage = build_storage()
        try:
            asset = asset_service.ingest_file(
                session,
                storage,
                user_id=job.user_id,
                source_path=downloaded.media,
                filename=_filename_for(meta.title, downloaded.media),
                name=meta.title,
                source=SOURCE_URL,
            )
        except asset_service.UnsupportedFile as exc:
            raise UrlImportError(
                f"The site delivered a {downloaded.media.suffix or 'nameless'} file, "
                "which the library does not accept"
            ) from exc

        progress("Recording where it came from", 94, "")
        _apply_metadata(session, asset, meta)
        _apply_thumbnail(session, storage, asset, downloaded.thumbnail)
        tag_note = _apply_tags(session, asset, meta, apply=request.apply_tags)
        clip_count = _chapters_to_clips(session, asset, meta) if request.chapters_as_clips else 0

        captioned = None
        if downloaded.captions is not None:
            progress("Reading captions", 97, "")
            if _store_captions(session, asset, meta, downloaded):
                captioned = asset

    detail = "Imported"
    extras = [
        note
        for note in (
            tag_note,
            f"{clip_count} chapter clip{'' if clip_count == 1 else 's'}" if clip_count else "",
            "captions as transcript" if captioned else "",
        )
        if note
    ]
    if extras:
        detail += " · " + ", ".join(extras)

    return ImportResult(detail=detail, created_asset_id=asset.id, captioned_asset=captioned)


# ─── playlists ───────────────────────────────────────────────────────────────


def _fan_out(
    session: Session, job: EnrichmentJob, request: ImportRequest, info: dict
) -> ImportResult:
    """Queue one import per playlist entry, and finish.

    One job per video rather than one job looping over them, for the reasons
    `bulk.py` gives in the other direction and that point this way here: each download
    is long, independently fallible, and independently cancellable, and a failure on
    video 30 of 50 should cost video 30, not videos 31 to 50. Each child gets its own row
    in the activity feed, and its own asset.

    Entries already in the library are skipped before queueing, so pasting the same
    playlist next month fetches only what has been added since.
    """
    # Imported lazily: `app.jobs.enrichment` imports this module to dispatch to it.
    from app.jobs import enrichment as enrichment_jobs
    from app.models.job import KIND_IMPORT_URL

    if request.depth >= MAX_PLAYLIST_DEPTH:
        raise UrlImportError(
            "That playlist is nested too deeply to import in one go. "
            "Paste one of the playlists inside it instead."
        )

    title = str(info.get("title") or "").strip()
    if title:
        set_fields(session, job, asset_name=title[:255])

    entries = ytdlp.playlist_entries(info)
    if not entries:
        raise UrlImportError("That playlist has nothing in it that can be imported")

    existing = _already_imported(session, job.user_id, [url for url, _ in entries])

    queued = skipped = 0
    for url, entry_title in entries:
        if url in existing:
            skipped += 1
            continue
        child = ImportRequest(
            url=url,
            audio_only=request.audio_only,
            apply_tags=request.apply_tags,
            chapters_as_clips=request.chapters_as_clips,
            depth=request.depth + 1,
        )
        enrichment_jobs.submit_library(
            session,
            job.user_id,
            KIND_IMPORT_URL,
            payload=child.encode(),
            asset_name=(entry_title or url)[:255],
        )
        queued += 1

    if queued:
        detail = f"Queued {queued} import{'' if queued == 1 else 's'}"
        if skipped:
            detail += f", {skipped} already in your library"
    else:
        detail = f"All {skipped} already in your library"

    total = info.get("playlist_count")
    if isinstance(total, int) and total > len(entries):
        detail += f" (the first {len(entries)} of {total})"
    return ImportResult(detail=detail)


def _already_imported(session: Session, user_id: str, urls: Iterable[str]) -> dict[str, str]:
    """{source_url: asset id} for whichever of these this user already has.

    Matched on the asset's own `source_url` — which a clip leaves blank and inherits —
    so it finds the original, never one of its clips.
    """
    wanted = {u for u in urls if u}
    if not wanted:
        return {}
    rows = session.exec(
        select(Asset.source_url, Asset.id)
        .where(Asset.user_id == user_id, col(Asset.source_url).in_(wanted))
        .order_by(col(Asset.upload_date))
    ).all()
    found: dict[str, str] = {}
    for source_url, asset_id in rows:
        found.setdefault(source_url, asset_id)
    return found


# ─── after the file is in ────────────────────────────────────────────────────


def _filename_for(title: str, media: Path) -> str:
    """A display filename: the title, with the downloaded file's real extension.

    The extension decides the asset type, so it comes from what yt-dlp actually wrote.
    Slashes are replaced rather than left to `sanitize_original_name`, which would take
    only the part after the last one — "AC/DC live" would be stored as "DC live".
    """
    stem = title.replace("/", "-").replace("\\", "-").strip() or "download"
    return f"{stem[:200]}{media.suffix.lower()}"


def _apply_metadata(session: Session, asset: Asset, meta: ImportedMetadata) -> None:
    """What the site says about the video, stamped `embedded`.

    `embedded` rather than `human` or `ai`: it is a statement of fact from the source
    that nobody here has checked — the same standing as an EXIF Artist, which is what
    that stamp means. The row is minutes old, so nothing a person wrote can be
    overwritten; what can be is whatever the harvester read out of the file's own
    container tags, and the site is the better authority on its own video.

    `retrieved_at` is the one attribution field only an import can know, and the reason
    M10 left it without an automatic writer until now.
    """
    changes: dict = dict(meta.attribution)
    changes["retrieved_at"] = utcnow()
    if meta.description:
        changes["description"] = meta.description
    try:
        asset_service.apply_embedded_metadata(session, asset, changes)
    except Exception:  # noqa: BLE001 - the video is in; losing its metadata must not undo that
        logger.warning("Could not store imported metadata for %s", asset.id, exc_info=True)


def _apply_thumbnail(session: Session, storage, asset: Asset, source: Optional[Path]) -> None:
    """Use the site's own thumbnail instead of a frame GAM picked.

    The one the uploader chose is the picture people remember the video by, which is
    what a library grid is for. It matters most for audio-only imports, which otherwise
    have no picture at all. Written over the generated poster at the same key, so
    nothing else — the clips made next copy this `thumb_key` — needs to know.
    """
    if source is None or not asset.storage_key:
        return
    preview = thumbnails.generate(source, TYPE_IMAGE, source.name)
    if not preview:
        return
    try:
        stored = storage.write_bytes(thumb_key_for(asset.storage_key), preview)
    except (StorageError, OSError):
        logger.warning("Could not store the site thumbnail for %s", asset.id, exc_info=True)
        return
    asset.thumb_key = stored.key
    session.add(asset)
    session.commit()
    session.refresh(asset)


def _apply_tags(session: Session, asset: Asset, meta: ImportedMetadata, *, apply: bool) -> str:
    """The uploader's tags — applied, or offered as suggestions.

    Applied by default, which is the user's decision and differs from autotag's rule that
    nothing is ever applied silently (FR 9.1.4). That rule exists because a model's tags
    are a guess; these are what the uploader chose to call their own video, closer to
    embedded metadata than to inference. Unticking "Apply the uploader's tags" on the
    import form routes them through the suggestion queue instead, for channels that pad
    their tags for search.
    """
    if not meta.tags:
        return ""
    try:
        if not apply:
            count = suggestion_service.propose(
                session,
                asset,
                tags=meta.tags,
                title=None,
                model=f"{meta.extractor or 'site'} tags",
            )
            return f"{count} tag suggestion{'' if count == 1 else 's'}" if count else ""

        attached = 0
        for name in meta.tags:
            tag = tag_service.get_or_create(session, asset.user_id, name)
            if tag is not None and tag_service.attach(session, asset.id, tag.id):
                attached += 1
        asset_service.reindex_ids(session, [asset.id])
        return f"{attached} tag{'' if attached == 1 else 's'}" if attached else ""
    except Exception:  # noqa: BLE001 - see _apply_metadata
        logger.warning("Could not apply imported tags to %s", asset.id, exc_info=True)
        return ""


def _chapters_to_clips(session: Session, asset: Asset, meta: ImportedMetadata) -> int:
    """One non-destructive clip per chapter.

    Clamped to the probed duration: sites round, and a last chapter ending a fraction of
    a second past the end of the file would otherwise be refused by `create_clip`. A lone
    chapter covering the whole video is skipped — it would be a clip identical to its
    parent.

    Named "<chapter> — <video>" because a clip appears in the library grid and in search
    results on its own, where "Introduction" says nothing.
    """
    chapters = meta.chapters
    if len(chapters) < 2:
        return 0

    duration = asset.duration_seconds
    created = 0
    for chapter in chapters:
        start = max(0.0, chapter.start)
        end = chapter.end if chapter.end is not None else duration
        if end is None:
            continue
        if duration:
            end = min(end, duration)
        if end - start < MIN_CHAPTER_SECONDS:
            continue
        label = chapter.title or f"Part {created + 1}"
        try:
            asset_service.create_clip(
                session,
                asset,
                in_point=start,
                out_point=end,
                name=f"{label} — {asset.name}"[:255],
            )
            created += 1
        except ValueError as exc:
            logger.info("Skipped chapter %r of %s: %s", label, asset.id, exc)
        except Exception:  # noqa: BLE001 - see _apply_metadata
            logger.warning("Could not clip chapter %r of %s", label, asset.id, exc_info=True)
    return created


def _store_captions(
    session: Session, asset: Asset, meta: ImportedMetadata, downloaded: ytdlp.Downloaded
) -> bool:
    """The site's captions as this asset's transcript. Returns whether any were stored.

    The model name records which kind they were, so the transcript panel — and whoever
    wonders why a transcript has no speakers — can tell a creator's own subtitles from a
    machine's.
    """
    try:
        text = downloaded.captions.read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.warning("Could not read downloaded captions for %s", asset.id, exc_info=True)
        return False

    segments = caption_parser.parse_vtt(text)
    if not segments:
        return False

    site = meta.extractor or "site"
    kind = "auto-captions" if downloaded.captions_automatic else "captions"
    language = downloaded.captions_language.removesuffix("-orig")
    try:
        store_transcript(
            session,
            asset,
            Transcript(segments=segments, language=language, model=f"{site}-{kind}"),
        )
    except Exception:  # noqa: BLE001 - see _apply_metadata
        logger.warning("Could not store captions for %s", asset.id, exc_info=True)
        return False
    return True
