"""Importing tracks from a music library's web page: preview, then one job per track.

Two steps, because a page can list a thousand tracks and the person wants twelve. The
preview reads the page and returns what it found, synchronously, for a review list; the
import queues one job per ticked track, as a playlist import does, so a failure on track
eight costs track eight and each one is cancellable.

What is found is described in `ingest/page_tracks.py`. This module adds the parts that
need a session: the AI fallback (a provider, and usage to record), recognising what is
already in the library, and writing what the page said onto the imported asset.

Only ticked tracks are downloaded, one at a time on the enrichment queue. A music
library's licence is about using its tracks, and some forbid bulk downloading; one person
importing what they chose, at the pace of one worker, is the use those licences describe.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import tempfile
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urljoin

from sqlmodel import Session, col, func, select

from app import music
from app.clock import utcnow
from app.ingest import page_tracks, web
from app.ingest.embedded_metadata import normalise_partial_date
from app.ingest.filetypes import SOURCE_URL, TYPE_AUDIO
from app.ingest.page_tracks import PageExtraction, PageTrack
from app.models.asset import Asset
from app.models.job import EnrichmentJob
from app.providers import build_provider
from app.providers.base import ProviderError
from app.services import assets as asset_service
from app.services import tags as tag_service
from app.storage import build_storage, capacity
from app.usage import events as usage_events

logger = logging.getLogger(__name__)

Progress = Callable[..., None]

# One import request's worth. A review list of fifty ticked tracks is already an
# afternoon of listening; more at once is a bulk download, not a choice.
MAX_TRACKS_PER_IMPORT = 50

# The longest page the model is shown, in characters, and the most tracks it may return.
AI_PAGE_CHARS = 60_000
AI_MAX_TRACKS = 100

AI_SYSTEM = (
    "You read a web page from a music library and list the music tracks on it as JSON. "
    "You only report what the page states. You never invent a URL."
)


class PageImportError(Exception):
    """The page could not be read, or an import job could not run. Worded for the user."""


# ─── preview ─────────────────────────────────────────────────────────────────


def preview(session: Session, user_id: str, url: str, *, use_ai: bool = True) -> PageExtraction:
    """Read a page and return the tracks on it, best source first."""
    try:
        final_url, page = web.fetch_text(url)
        extraction = page_tracks.extract_structured(final_url, page, web.fetch_text)
    except web.WebFetchError as exc:
        raise PageImportError(str(exc)) from exc

    if extraction is None and use_ai:
        provider = build_provider(session, user_id)
        if provider is not None:
            extraction = _ask_ai(session, user_id, provider, final_url, page)

    if extraction is None or not extraction.tracks:
        links = page_tracks.extract_links(final_url, page)
        if links.tracks:
            if extraction is not None and extraction.method == page_tracks.METHOD_AI:
                links.note = "The AI found no tracks it could tie to a file, so these are the bare links."
            elif use_ai:
                links.note = (
                    "No AI provider is configured, so these are the bare audio links. "
                    "Add one in Settings to read titles and details from the page."
                )
            extraction = links
        else:
            extraction = PageExtraction(
                final_url,
                page_tracks.METHOD_LINKS,
                [],
                "links",
                note=(
                    "No audio files were found on this page. If the site builds its track "
                    "list with JavaScript after loading, the files are not in the page itself."
                ),
            )
    return extraction


def already_imported(session: Session, user_id: str, tracks: list[PageTrack]) -> dict[str, str]:
    """{audio_url: asset id} for the tracks this library already has."""
    keys: dict[str, str] = {}
    for track in tracks:
        keys[track.source_url or track.audio_url] = track.audio_url
        keys[track.audio_url] = track.audio_url
    if not keys:
        return {}
    rows = session.exec(
        select(Asset.source_url, Asset.id).where(
            Asset.user_id == user_id, col(Asset.source_url).in_(list(keys))
        )
    ).all()
    return {keys[source_url]: asset_id for source_url, asset_id in rows}


def similar_in_library(session: Session, user_id: str, tracks: list[PageTrack]) -> dict[str, str]:
    """{audio_url: asset id} for tracks whose title (and artist, when both are known)
    match an audio file already in the library.

    A warning, not a refusal. The same piece downloaded from somewhere else is a
    different file, so neither its URL nor its checksum can recognise it; its name can.
    Names also collide by accident ("Intro"), which is why this only flags.
    """
    titles = {t.title.strip().lower() for t in tracks if t.title.strip()}
    if not titles:
        return {}
    rows = session.exec(
        select(Asset.id, Asset.name, Asset.creator).where(
            Asset.user_id == user_id,
            Asset.asset_type == TYPE_AUDIO,
            col(Asset.storage_key).is_not(None),
            func.lower(Asset.name).in_(titles),
        )
    ).all()
    by_title: dict[str, list[tuple[str, Optional[str]]]] = {}
    for asset_id, name, creator in rows:
        by_title.setdefault(name.strip().lower(), []).append((asset_id, creator))

    found: dict[str, str] = {}
    for track in tracks:
        for asset_id, creator in by_title.get(track.title.strip().lower(), []):
            if track.creator and creator and track.creator.strip().lower() != creator.strip().lower():
                continue
            found[track.audio_url] = asset_id
            break
    return found


def _same_file(session: Session, user_id: str, path: Path) -> Optional[Asset]:
    """An asset in this library with exactly these bytes, if there is one.

    The checksum every stored file already carries is what catches a duplicate the URL
    cannot: the same MP3 from a mirror, a different link to one file, or a track
    uploaded by hand before it was imported.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return session.exec(
        select(Asset).where(
            Asset.user_id == user_id, Asset.checksum_sha256 == digest.hexdigest()
        )
    ).first()


# ─── the AI fallback ─────────────────────────────────────────────────────────


def _ask_ai(session: Session, user_id: str, provider, page_url: str, page: str) -> Optional[PageExtraction]:
    """Ask the provider to list the tracks, holding it to the audio URLs the page has.

    The grounding rule is the point. A model asked for download links will produce
    plausible ones, and a plausible URL that is not on the page is either a 404 or a file
    nobody chose. Every URL it returns is checked against the page's own audio URLs, and
    an entry whose URL is not among them is dropped.
    """
    candidates = page_tracks.candidate_audio_urls(page_url, page)
    if not candidates:
        return None

    prompt = (
        f"Page URL: {page_url}\n\n"
        "Audio file URLs found on the page (the only URLs you may use):\n"
        + "\n".join(candidates[:400])
        + "\n\nPage text, with link targets written as [link: …]:\n"
        + page_tracks.page_text(page, AI_PAGE_CHARS)
        + "\n\nList each music track on this page as a JSON array, one object per track, "
        "with these keys, leaving out any the page does not state:\n"
        '  "audio_url" (exactly one of the URLs above), "title", "artist", "album", '
        '"description", "duration" (seconds), "bpm", "genres" (list), "moods" (list), '
        '"instruments" (list), "tags" (list), "license", "credit" (attribution text the '
        'page asks for), "page_url" (the track\'s own page, if linked), "published" '
        "(YYYY, YYYY-MM or YYYY-MM-DD).\n"
        "Answer with the JSON array only."
    )
    try:
        completion = provider.complete(prompt, system=AI_SYSTEM)
    except ProviderError as exc:
        raise PageImportError(f"The AI provider could not read the page: {exc}") from exc
    usage_events.record_completion(session, None, user_id, completion)

    allowed = set(candidates)
    tracks = [t for t in (_track_from_ai(page_url, item) for item in _json_array(completion.text)) if t]
    tracks = [t for t in tracks if t.audio_url in allowed]
    unique = list({t.audio_url: t for t in tracks}.values())[:AI_MAX_TRACKS]
    return PageExtraction(page_url, page_tracks.METHOD_AI, unique, "AI")


def _json_array(text: str) -> list[Any]:
    """The first JSON array in a reply, tolerating a code fence or a sentence around it."""
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        value = json.loads(text[start : end + 1])
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _track_from_ai(page_url: str, item: Any) -> Optional[PageTrack]:
    if not isinstance(item, dict):
        return None
    audio_url = item.get("audio_url")
    title = _short(item.get("title"), 255)
    if not isinstance(audio_url, str) or not title:
        return None
    audio_url = urljoin(page_url, audio_url.strip())
    track_page = item.get("page_url")
    duration = item.get("duration")
    return PageTrack(
        audio_url=audio_url,
        title=title,
        creator=_short(item.get("artist"), 500),
        source_title=_short(item.get("album"), 500),
        description=_short(item.get("description"), 2000),
        duration_seconds=float(duration) if isinstance(duration, (int, float)) and duration > 0 else None,
        published_date=_partial_date(item.get("published")),
        license=_short(item.get("license"), 500),
        credit_line=_short(item.get("credit"), 2000),
        source_url=urljoin(page_url, track_page) if isinstance(track_page, str) and track_page else audio_url,
        genres=_list(item.get("genres")),
        moods=_list(item.get("moods")),
        instruments=_list(item.get("instruments")),
        tags=_list(item.get("tags")),
        bpm=music.parse_bpm(item.get("bpm")),
    )


def _short(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:limit] or None


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return music.clean_terms(v for v in value if isinstance(v, str))
    if isinstance(value, str):
        return music.split_terms(value)
    return []


def _partial_date(value: Any) -> Optional[str]:
    return normalise_partial_date(value) if isinstance(value, str) else None


# ─── the import job ──────────────────────────────────────────────────────────


def encode_track(track: PageTrack) -> str:
    return json.dumps(track.to_dict(), sort_keys=True)


def decode_track(payload: Optional[str]) -> PageTrack:
    try:
        data = json.loads(payload or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data = data.get("track", data)
    known = {f.name for f in fields(PageTrack)}
    values = {k: v for k, v in data.items() if k in known}
    if not isinstance(values.get("audio_url"), str) or not isinstance(values.get("title"), str):
        raise PageImportError("This import job has no track to fetch")
    return PageTrack(**values)


@dataclass
class TrackImportResult:
    detail: str
    created_asset_id: Optional[str] = None


def run_track(session: Session, job: EnrichmentJob, progress: Progress) -> TrackImportResult:
    """Download one track and catalogue it with what its page said."""
    track = decode_track(job.payload)

    existing = already_imported(session, job.user_id, [track])
    if track.audio_url in existing:
        return TrackImportResult("Already in your library", existing[track.audio_url])

    if not capacity.has_room():
        raise PageImportError(capacity.DISK_FULL_MESSAGE)

    with tempfile.TemporaryDirectory(prefix="gam-track-") as workdir:
        progress("Downloading", 5, "")

        def report(received: int, total: Optional[int]) -> None:
            if total:
                progress("Downloading", 5 + int(80 * received / total), "")

        try:
            path = web.download_audio(track.audio_url, Path(workdir), "track", on_progress=report)
        except web.WebFetchError as exc:
            raise PageImportError(str(exc)) from exc

        progress("Checking for a copy already here", 86, "")
        duplicate = _same_file(session, job.user_id, path)
        if duplicate is not None:
            # The download is thrown away with the temp directory. What the page said
            # still fills the copy's blanks, so a track uploaded bare gains its credits
            # and its profile, and its source URL is recorded for next time.
            apply_track_metadata(session, duplicate, track, fill_blanks_only=True)
            return TrackImportResult(
                f"Already in your library as “{duplicate.name}” (the same file)",
                duplicate.id,
            )

        progress("Saving", 88, "")
        stem = re.sub(r"[\\/]+", "-", track.title).strip() or "track"
        try:
            asset = asset_service.ingest_file(
                session,
                build_storage(),
                user_id=job.user_id,
                source_path=path,
                filename=f"{stem[:200]}{path.suffix.lower()}",
                name=track.title,
                source=SOURCE_URL,
            )
        except asset_service.UnsupportedFile as exc:
            raise PageImportError("The site sent a file the library does not accept") from exc

    progress("Recording what the page said", 95, "")
    tag_count = apply_track_metadata(session, asset, track)
    detail = "Imported"
    if tag_count:
        detail += f" · {tag_count} tag{'' if tag_count == 1 else 's'}"
    return TrackImportResult(detail, asset.id)


def apply_track_metadata(
    session: Session, asset: Asset, track: PageTrack, *, fill_blanks_only: bool = False
) -> int:
    """What the page said about the track, stamped `embedded`, and its tags attached.

    `embedded` for the same reason an imported video's site metadata is: a statement from
    the source that nobody here checked. On a fresh import it is written over whatever
    the file's own tags said: the site describing its own track is the better authority.
    On a duplicate (`fill_blanks_only`) the asset may hold a person's corrections, so it
    only fills what is empty. Returns the tag count.
    """
    changes: dict[str, Any] = {
        "source_url": track.source_url or track.audio_url,
        "retrieved_at": utcnow(),
    }
    for name in ("creator", "publisher", "source_title", "license", "credit_line", "description"):
        value = getattr(track, name)
        if value:
            changes[name] = value
    # Normalised here, not trusted: the track came back from the browser, and the date
    # filters compare this column as text, which only works for ISO partial dates.
    published = normalise_partial_date(track.published_date) if track.published_date else None
    if published:
        changes["published_date"] = published
    if track.genres:
        changes["music_genres"] = music.encode_terms(track.genres)
    if track.moods:
        changes["music_moods"] = music.encode_terms(track.moods)
    if track.instruments:
        changes["music_instruments"] = music.encode_terms(track.instruments)
    if track.bpm is not None:
        changes["music_bpm"] = track.bpm

    if fill_blanks_only:
        changes = {
            name: value
            for name, value in changes.items()
            if getattr(asset, name, None) in (None, "") and name != "retrieved_at"
        }
    try:
        if changes:
            asset_service.apply_embedded_metadata(session, asset, changes)
    except Exception:  # noqa: BLE001 - the track is in; losing its metadata must not undo that
        logger.warning("Could not store page metadata for %s", asset.id, exc_info=True)

    attached = 0
    try:
        for name in track.tags:
            tag = tag_service.get_or_create(session, asset.user_id, name)
            if tag is not None and tag_service.attach(session, asset.id, tag.id):
                attached += 1
        if attached:
            asset_service.reindex_ids(session, [asset.id])
    except Exception:  # noqa: BLE001 - same
        logger.warning("Could not attach page tags to %s", asset.id, exc_info=True)
    return attached
