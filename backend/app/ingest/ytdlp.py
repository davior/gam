"""Everything GAM asks of yt-dlp, in one module.

The only file that imports `yt_dlp`. yt-dlp's option names and info-dict keys are a
large, loosely documented surface that shifts between releases, and keeping every use of
it here means a release that renames something is one file to fix — and one fake to
update in the tests, which never touch the network.

Three calls: `probe_url` reads what a link is, `download` fetches it, and
`metadata_from_info` (pure) maps what the site said onto GAM's fields. The job that
strings them together is `enrichment/import_url.py`.

Why these particular options is recorded beside each one. The two that matter most:

- **`allowed_extractors` excludes `generic`.** yt-dlp knows ~1,800 sites by name; its
  generic extractor is the fallback that scrapes *any* page for something playable. That
  fallback is the part a user-supplied URL could aim at an internal address, and it is
  also the part most likely to import the wrong thing. Named sites only.
- **No `ignoreerrors`.** Every failure raises, so a cancel raised from a progress hook
  unwinds the download instead of being logged and skipped past.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import yt_dlp
from fastapi import HTTPException, status
from yt_dlp.utils import DownloadCancelled, DownloadError

from app.config import settings
from app.ingest.embedded_metadata import normalise_partial_date
from app.jobs.runner import JobCancelled
from app.safe_url import require_safe_external_url

logger = logging.getLogger(__name__)

Progress = Callable[[str, int, str], None]

# The same ceilings AssetUpdate puts on a hand-typed value, so an import cannot store
# something a person editing the field afterwards would be refused.
MAX_DESCRIPTION = 20_000
MAX_ATTRIBUTION = 500
MAX_URL = 2_000
# The tag API's own limits (schemas_tags.py). A YouTube video can carry up to 500
# characters of tags, which lands well under fifty in practice.
MAX_TAG_LENGTH = 80
MAX_TAGS = 50

# What a download may be left as. Anything else is remuxed to MP4 — a stream copy, not a
# re-encode — so the library never receives a container it would refuse (`.ts`, `.3gp`)
# or one browsers play badly (`.flv`). Audio stays audio: without its own entries here, a
# podcast's `.mp3` would be remuxed into an `.mp4` and catalogued as video.
_KEEP_CONTAINERS = ("mp4", "m4v", "webm", "mkv", "mov", "m4a", "mp3", "ogg", "opus", "flac", "wav", "aac")
_REMUX_MAPPING = "/".join(f"{ext}>{ext}" for ext in _KEEP_CONTAINERS) + "/mp4"


# An explicit scheme: letters, then a colon *not* followed by a digit. The digit rule is
# what tells `javascript:…` (a scheme, refused) from `example.com:8443/…` (a host and a
# port, which gets https:// put in front like any other bare link).
_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*:(?!\d)", re.IGNORECASE)


class UrlImportError(Exception):
    """Something the user can be told about — the job's error message, verbatim."""


# ─── the URL ─────────────────────────────────────────────────────────────────


def normalise_url(raw: str) -> str:
    """The URL as it will be fetched, or an HTTPException saying why not.

    Forgiving about the two ways people actually paste links — `youtu.be/…` with no
    scheme, and the occasional `http://` — and strict about everything else. Every site
    worth importing from serves https, so upgrading costs nothing and lets
    `require_safe_external_url` keep its https-only rule rather than growing an exception.
    """
    url = (raw or "").strip()
    if not url:
        _invalid("Paste a link to import")
    if not _SCHEME.match(url):
        url = f"https://{url}"

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() == "http":
        url = urllib.parse.urlunsplit(parsed._replace(scheme="https"))
    elif parsed.scheme.lower() != "https":
        _invalid("Only web links (https://…) can be imported")
    try:
        parsed.port
    except ValueError:
        _invalid("That link has an invalid port")

    if len(url) > MAX_URL:
        _invalid("That link is too long")

    # Checked here as well as by `allowed_extractors`: that excludes the extractor that
    # fetches arbitrary pages, and this refuses a named-site URL whose host resolves
    # inward. Neither alone is the whole of it.
    require_safe_external_url(url)
    return url


def _invalid(message: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "invalid_url", "message": message},
    )


# ─── reading a link ──────────────────────────────────────────────────────────


def probe_url(url: str, workdir: Path) -> dict:
    """What `url` is — one video, or a playlist of them — without downloading.

    Playlists come back flat (`extract_flat="in_playlist"`): each entry is a URL and a
    title, not a fully resolved video. Resolving two hundred entries here would take
    minutes and spend two hundred page loads on videos that are about to be queued as
    their own jobs, which resolve them anyway.
    """
    options = {
        **_base_options(workdir),
        "extract_flat": "in_playlist",
        "playlist_items": f"1:{max(1, settings.url_import_max_playlist_items)}",
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except DownloadError as exc:
            raise UrlImportError(readable_error(exc)) from exc
    if not info:
        raise UrlImportError("Nothing importable was found at that link")
    return info


def is_playlist(info: dict) -> bool:
    return info.get("_type") in ("playlist", "multi_video")


def playlist_entries(info: dict) -> list[tuple[str, str]]:
    """(url, title) for each playlist entry that has an absolute URL.

    Entries without one are skipped rather than guessed at: some extractors hand back a
    bare id there, and turning that into a URL is the extractor's knowledge, not ours.
    """
    found = []
    for entry in info.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url") or entry.get("webpage_url") or ""
        if "://" not in url:
            continue
        found.append((url, str(entry.get("title") or "").strip()))
    return found


def refuse_if_live(info: dict) -> None:
    """A live stream would download until it ended — which may be never."""
    live_status = info.get("live_status")
    if info.get("is_live") or live_status == "is_live":
        raise UrlImportError("That is a live stream. Import it once it has finished.")
    if live_status == "is_upcoming":
        raise UrlImportError("That stream has not started yet.")
    if live_status == "post_live":
        raise UrlImportError(
            "That stream has only just ended and is still being processed. "
            "Try again in an hour or two."
        )


# ─── downloading ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Downloaded:
    media: Path
    thumbnail: Optional[Path] = None
    captions: Optional[Path] = None
    captions_language: str = ""
    # Machine captions rather than ones somebody wrote. Recorded on the transcript as
    # its model, so it is visible which kind a search is matching against.
    captions_automatic: bool = False


@dataclass(frozen=True)
class CaptionChoice:
    language: str
    automatic: bool


def download(
    url: str,
    info: dict,
    workdir: Path,
    *,
    audio_only: bool,
    want_captions: bool,
    progress: Progress,
) -> Downloaded:
    """Fetch the media, its thumbnail and — when asked — its captions into `workdir`.

    `info` is the probe's answer, read here only to choose a caption track. The download
    itself extracts the page again rather than re-processing that dict. Re-processing
    would save a few seconds, and it is what `--load-info-json` does — but a resolved
    info dict can hold format URLs that have already expired and fragment lists that do
    not survive a round trip, and this path cannot be exercised against the real sites
    in CI. A fresh `extract_info(download=True)` is the call every yt-dlp user makes,
    which makes it the one least likely to break quietly.
    """
    captions = choose_captions(info) if want_captions else None

    options: dict[str, Any] = {
        **_base_options(workdir),
        "paths": {"home": str(workdir), "temp": str(workdir)},
        # Fixed names: the title is recorded as metadata, and a filename built from it
        # is a filename built from text the site controls.
        "outtmpl": {
            "default": "media.%(ext)s",
            "thumbnail": "thumb.%(ext)s",
            "subtitle": "captions.%(ext)s",
        },
        # No conversion: Pillow reads the jpg/webp/png sites serve, and
        # `thumbnails.generate` re-encodes to the library's JPEG anyway.
        "writethumbnail": True,
        "progress_hooks": [_progress_hook(progress)],
        "postprocessor_hooks": [_postprocessor_hook(progress)],
    }

    if audio_only:
        options["format"] = "ba[ext=m4a]/ba/b"
        options["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]
    else:
        # `format_sort` rather than a filter: the tallest stream at or under the cap wins,
        # H.264 over VP9/AV1 at the same height (Safari will not play the latter from an
        # MP4), and a video with nothing under the cap still downloads its smallest
        # stream instead of failing outright.
        options["format"] = "bv*+ba/b"
        options["format_sort"] = [
            f"res:{max(144, settings.url_import_max_height)}",
            "vcodec:h264",
            "acodec:m4a",
        ]
        options["merge_output_format"] = "mp4"
        options["postprocessors"] = [
            {"key": "FFmpegVideoRemuxer", "preferedformat": _REMUX_MAPPING},
        ]

    if captions:
        options["subtitleslangs"] = [captions.language]
        options["subtitlesformat"] = "vtt/best"
        if captions.automatic:
            options["writeautomaticsub"] = True
        else:
            options["writesubtitles"] = True
        options["postprocessors"].append(
            {"key": "FFmpegSubtitlesConvertor", "format": "vtt", "when": "before_dl"}
        )

    with yt_dlp.YoutubeDL(options) as ydl:
        try:
            result = ydl.extract_info(url, download=True)
        except DownloadCancelled as exc:
            raise JobCancelled(str(exc)) from exc
        except DownloadError as exc:
            raise UrlImportError(readable_error(exc)) from exc

    media = _media_path(result, workdir)
    return Downloaded(
        media=media,
        thumbnail=_first(workdir, ("thumb.jpg", "thumb.jpeg", "thumb.webp", "thumb.png")),
        captions=_first_glob(workdir, "captions*.vtt") if captions else None,
        captions_language=captions.language if captions else "",
        captions_automatic=bool(captions and captions.automatic),
    )


def choose_captions(info: dict) -> Optional[CaptionChoice]:
    """The one caption track worth storing as a transcript, if there is one.

    Written by a person beats machine-made, and the video's own language beats a
    translation — YouTube offers auto-translated tracks into a hundred languages, all of
    them worse than the original. YouTube marks its original-language speech recognition
    track `<lang>-orig`; the plain `<lang>` beside it may be a translation.
    """
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat" and v}
    automatic = {k: v for k, v in (info.get("automatic_captions") or {}).items() if v}
    spoken = (info.get("language") or "").strip()

    if spoken:
        for key in (spoken, *sorted(k for k in manual if k.split("-")[0] == spoken)):
            if key in manual:
                return CaptionChoice(key, automatic=False)
        for key in (f"{spoken}-orig", spoken):
            if key in automatic:
                return CaptionChoice(key, automatic=True)

    # The language is not stated. One hand-written track is almost certainly the
    # original; several are a guess, and English is the likeliest.
    if len(manual) == 1:
        return CaptionChoice(next(iter(manual)), automatic=False)
    for key in sorted(manual):
        if key.split("-")[0] == "en":
            return CaptionChoice(key, automatic=False)
    for key in sorted(automatic):
        if key.endswith("-orig"):
            return CaptionChoice(key, automatic=True)
    return None


def _media_path(result: Optional[dict], workdir: Path) -> Path:
    """Where the finished file ended up, after merging and any remux or extraction.

    `requested_downloads` carries the final path once postprocessors have run. The glob
    is the fallback for an extractor that does not fill it in, and excludes the sidecar
    and scratch files that share the directory.
    """
    for entry in (result or {}).get("requested_downloads") or []:
        path = entry.get("filepath")
        if path and Path(path).is_file():
            return Path(path)
    for candidate in sorted(workdir.glob("media.*")):
        if candidate.suffix.lower() not in (".part", ".ytdl", ".vtt", ".json") and candidate.is_file():
            return candidate
    raise UrlImportError("The download finished but produced no file")


def _first(workdir: Path, names: tuple[str, ...]) -> Optional[Path]:
    for name in names:
        path = workdir / name
        if path.is_file():
            return path
    return None


def _first_glob(workdir: Path, pattern: str) -> Optional[Path]:
    return next((p for p in sorted(workdir.glob(pattern)) if p.is_file()), None)


def _progress_hook(progress: Progress) -> Callable[[dict], None]:
    """Report download progress, at most about once a second.

    yt-dlp calls this on every chunk — hundreds of times a second on a fast line — and
    each report is a database write. It is also the cancellation checkpoint: the job's
    reporter raises `JobCancelled`, which is converted to `DownloadCancelled`, the
    exception yt-dlp's own code is written to let through untouched.
    """
    last = [0.0]

    def hook(state: dict) -> None:
        if state.get("status") != "downloading":
            return
        now = time.monotonic()
        if now - last[0] < 1.0:
            return
        last[0] = now

        done = state.get("downloaded_bytes") or 0
        total = state.get("total_bytes") or state.get("total_bytes_estimate") or 0
        fraction = min(1.0, done / total) if total else 0.0
        # A `bv*+ba` download is two files one after the other; saying which keeps the
        # bar dropping back to zero from reading as a restart.
        stream = (state.get("info_dict") or {}).get("vcodec")
        stage = "Downloading audio" if stream in (None, "none") else "Downloading video"
        detail = f"{done / 1_048_576:.0f} of {total / 1_048_576:.0f} MB" if total else ""
        try:
            progress(stage, 5 + int(fraction * 80), detail)
        except JobCancelled as exc:
            raise DownloadCancelled() from exc

    return hook


def _postprocessor_hook(progress: Progress) -> Callable[[dict], None]:
    """Say so while ffmpeg merges or converts — minutes, for a long video."""

    def hook(state: dict) -> None:
        if state.get("status") != "started":
            return
        name = state.get("postprocessor") or ""
        stage = "Merging audio and video" if name == "Merger" else "Converting"
        try:
            progress(stage, 88, "")
        except JobCancelled as exc:
            raise DownloadCancelled() from exc

    return hook


# ─── options and errors ──────────────────────────────────────────────────────


class _Logger:
    """Route yt-dlp's chatter into logging instead of the server's stdout.

    Errors are only logged: every one of them is also raised as a `DownloadError`, which
    is where the user hears about it.
    """

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        logger.info("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        logger.info("yt-dlp: %s", msg)


def _base_options(workdir: Path) -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _Logger(),
        "allowed_extractors": ["default", "-generic"],
        # A watch URL carrying `&list=` imports the video that was open, not the
        # playlist it happened to be in. A bare playlist URL is still a playlist.
        "noplaylist": True,
        "ignoreerrors": False,
        "socket_timeout": 30,
        "retries": 10,
        "fragment_retries": 10,
    }

    cookies = settings.url_import_cookies_file.strip()
    if cookies:
        source = Path(cookies)
        if not source.is_file():
            raise UrlImportError(
                f"URL_IMPORT_COOKIES_FILE is set to {cookies}, but there is no file there"
            )
        # A copy, because yt-dlp writes the jar back out when it closes — which fails on
        # the read-only mount the docs recommend, and would otherwise rewrite the user's
        # own file with whatever the site set during this one import.
        copy = workdir / "cookies.txt"
        shutil.copyfile(source, copy)
        options["cookiefile"] = str(copy)

    return options


_ERROR_PREFIX = re.compile(r"^(?:ERROR:\s*)?(?:\[[^\]]+\]\s*)?(?:[\w-]+:\s+)?")

# Messages worth replacing with what to actually do about them.
_NEEDS_COOKIES = ("confirm you're not a bot", "confirm you’re not a bot", "confirm your age", "sign in to confirm")
_NEEDS_UPDATE = (
    "requested format is not available",
    "signature",
    "nsig",
    "n challenge",
    "javascript runtime",
    "js runtime",
    "player response",
)


def readable_error(exc: Exception) -> str:
    """A yt-dlp failure as something a person can act on.

    yt-dlp's messages are written for a terminal user — `ERROR: [youtube] abc123: …` —
    and several of them tell that user to pass a command-line flag this app has no way
    to accept. Three cases get rewritten into what to do here instead; the rest keep
    yt-dlp's own words, minus the prefix.
    """
    message = str(getattr(exc, "msg", None) or exc).strip()
    lowered = message.lower()

    if "no suitable extractor" in lowered or "unsupported url" in lowered:
        return "That site is not one yt-dlp knows how to import from."
    if any(phrase in lowered for phrase in _NEEDS_COOKIES):
        return (
            "The site wants this server to sign in first (YouTube does this to many "
            "datacenter IPs, and for age-restricted videos). Export your browser's "
            "cookies for the site to a file and set URL_IMPORT_COOKIES_FILE — see "
            "docs/url-import.md."
        )

    first_line = message.splitlines()[0] if message else ""
    cleaned = _ERROR_PREFIX.sub("", first_line, count=1).strip() or "The import failed"
    if any(phrase in lowered for phrase in _NEEDS_UPDATE):
        cleaned += (
            " — this usually means the site changed and yt-dlp needs updating: bump its "
            "version in backend/requirements.txt and rebuild."
        )
    return cleaned


# ─── mapping what the site said ──────────────────────────────────────────────


@dataclass(frozen=True)
class Chapter:
    start: float
    end: Optional[float]
    title: str


@dataclass(frozen=True)
class ImportedMetadata:
    title: str
    description: Optional[str]
    # Only fields the site actually supplied. See `metadata_from_info` for the mapping.
    attribution: dict[str, Any]
    tags: list[str] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    # yt-dlp's name for the site ("youtube", "rumble"), for labelling where tags and
    # captions came from.
    extractor: str = ""

    @property
    def source_url(self) -> Optional[str]:
        return self.attribution.get("source_url")


def metadata_from_info(info: dict) -> ImportedMetadata:
    """Map a yt-dlp info dict onto GAM's fields. Pure.

    The attribution mapping follows M10's rule that a blank beats a guess:

    - `publisher` is the channel. On every video site that is the outlet — the BBC's
      channel, a podcast's feed — which is what the field means.
    - `creator` is filled only when the site names one (`creators`, `artists`: music
      and some podcasts). It is *not* copied from the channel. On a news or interview
      channel the channel is rarely the person speaking, and a creator field that
      confidently says "BBC News" is the fabricated citation M10 exists to prevent. The
      attribute job can propose the speaker from the transcript, with evidence.
    - `source_title` is the series or album when there is one, otherwise the video's
      own title. `name` gets the title too, but `name` is the user's to rename; the
      citation should keep what the work was actually published as.
    - `license` only when the site states one. YouTube reports Creative Commons and
      says nothing for its standard licence, and nothing is invented for it.
    """
    title = _text(info.get("title") or info.get("fulltitle")) or _text(info.get("id")) or "Untitled"

    description = _text(info.get("description"))
    if description:
        description = description[:MAX_DESCRIPTION]

    attribution: dict[str, Any] = {}

    def put(name: str, value: Any) -> None:
        cleaned = _text(value)
        if cleaned:
            attribution[name] = cleaned[: MAX_URL if name == "source_url" else MAX_ATTRIBUTION]

    put("source_url", info.get("webpage_url") or info.get("original_url"))
    put("publisher", info.get("channel") or info.get("uploader"))
    put("creator", _joined(info.get("creators") or info.get("artists")) or info.get("creator") or info.get("artist"))
    put("source_title", info.get("series") or info.get("album") or title)
    put("license", info.get("license"))

    published = normalise_partial_date(info.get("release_date") or info.get("upload_date"))
    if not published and info.get("release_year"):
        published = normalise_partial_date(str(info["release_year"]))
    if published:
        attribution["published_date"] = published

    return ImportedMetadata(
        title=title[:255],
        description=description,
        attribution=attribution,
        tags=_tags(info.get("tags")),
        chapters=_chapters(info.get("chapters")),
        extractor=_text(info.get("extractor_key") or info.get("extractor")).lower(),
    )


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _joined(values: Any) -> str:
    if not isinstance(values, (list, tuple)):
        return ""
    return ", ".join(v.strip() for v in values if isinstance(v, str) and v.strip())


def _tags(raw: Any) -> list[str]:
    """Uploader tags, cleaned: whitespace collapsed, case-insensitive duplicates and
    over-long ones dropped, capped at what one tag request may carry."""
    if not isinstance(raw, (list, tuple)):
        return []
    seen: set[str] = set()
    tags: list[str] = []
    for value in raw:
        name = " ".join(value.split()) if isinstance(value, str) else ""
        if not name or len(name) > MAX_TAG_LENGTH or name.lower() in seen:
            continue
        seen.add(name.lower())
        tags.append(name)
        if len(tags) >= MAX_TAGS:
            break
    return tags


def _chapters(raw: Any) -> list[Chapter]:
    if not isinstance(raw, (list, tuple)):
        return []
    chapters = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            start = float(entry.get("start_time") or 0.0)
            end = float(entry["end_time"]) if entry.get("end_time") is not None else None
        except (TypeError, ValueError):
            continue
        chapters.append(Chapter(start=start, end=end, title=_text(entry.get("title"))))
    return chapters
