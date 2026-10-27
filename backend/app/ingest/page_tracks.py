"""Finding the tracks on a web page, with what the page says about each.

For music libraries that list their tracks on a page: a mood, a genre, a search. Three
ways in, tried in order of how much they can be trusted:

1. **What the site publishes for machines.** schema.org `MusicRecording` data (JSON-LD),
   which Mixkit and many others put in the page for search engines, and incompetech's
   `pieces.json`, the file its own page loads. Both are the site's structured statement
   of what each track is, so nothing has to be guessed from layout.
2. **The AI provider**, for a page with neither (`enrichment/page_import.py`). It reads
   the page's text and is only allowed to name audio URLs that actually appear in it.
3. **Bare audio links**, when there is no AI provider: every `.mp3` the page links to,
   named by its link text. Little metadata, but something to tick.

Pure apart from `fetch_text`, which is passed in, so the adapters are tested on saved
pages with no network.

A site-specific adapter exists only where the structured data leaves out something worth
having: Mixkit's JSON-LD has one genre per track, while its cards also link each track's
moods, instruments and tags. Each adapter names its site, and a site that changes its
layout breaks that adapter alone; the JSON-LD underneath keeps working.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Optional
from urllib.parse import quote, unquote, urljoin, urlsplit

from app import music
from app.ingest.embedded_metadata import normalise_partial_date
from app.ingest.filetypes import AUDIO_EXTENSIONS, extension_of

METHOD_STRUCTURED = "structured"
METHOD_AI = "ai"
METHOD_LINKS = "links"

# How many a single review list shows. Incompetech's catalogue page lists all of it,
# well over a thousand; the review is filtered client-side, and this only stops a
# pathological page from producing an unbounded response.
MAX_TRACKS = 2500
MAX_TEXT = 500
MAX_DESCRIPTION = 2000

FetchText = Callable[[str], tuple[str, str]]


@dataclass
class PageTrack:
    """One track as a page describes it. Every field but the title optional.

    `audio_url` is always set on a live page import, which downloads from it. A saved page
    can lack one (Pixabay's download is a button, not a link): the file then comes from
    the person, matched to the track by `match_id` or title (`match_files`).
    """

    audio_url: Optional[str]
    title: str
    creator: Optional[str] = None
    publisher: Optional[str] = None
    source_title: Optional[str] = None
    description: Optional[str] = None
    duration_seconds: Optional[float] = None
    published_date: Optional[str] = None
    license: Optional[str] = None
    credit_line: Optional[str] = None
    # Where to cite, and how a re-import is recognised. The track's own page when the
    # site has one, else the audio file itself.
    source_url: Optional[str] = None
    genres: list[str] = field(default_factory=list)
    moods: list[str] = field(default_factory=list)
    instruments: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    bpm: Optional[float] = None
    # A site's own id for the track, when its downloaded files carry it in their names
    # (Pixabay's do: `artist-title-604094.mp3`). What matches a saved page to the files.
    match_id: Optional[str] = None

    @property
    def key(self) -> str:
        """What identifies this track within one page, with or without an audio URL."""
        return self.audio_url or self.source_url or self.title

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PageExtraction:
    page_url: str
    method: str
    tracks: list[PageTrack]
    # Which adapter or source produced them, for the review screen: "incompetech",
    # "mixkit", "schema.org", "AI", "links".
    source: str
    note: str = ""


# ─── entry point ─────────────────────────────────────────────────────────────


def extract_structured(page_url: str, page: str, fetch_text: FetchText) -> Optional[PageExtraction]:
    """Tracks the site states outright. None when it states none."""
    host = (urlsplit(page_url).hostname or "").lower()

    if _is_host(host, "incompetech.com") and "pieces.json" in page:
        tracks = _incompetech(page_url, page, fetch_text)
        if tracks:
            return PageExtraction(page_url, METHOD_STRUCTURED, tracks, "incompetech")

    tracks = _json_ld_tracks(page_url, page)
    if tracks:
        source = "schema.org"
        if _is_host(host, "mixkit.co"):
            _enrich_mixkit(page_url, page, tracks)
            source = "mixkit"
        return PageExtraction(page_url, METHOD_STRUCTURED, tracks[:MAX_TRACKS], source)

    return None


def extract_links(page_url: str, page: str) -> PageExtraction:
    """Every audio file the page links to, named by its link text. The last resort."""
    tracks: list[PageTrack] = []
    seen: set[str] = set()
    for href, text in _audio_anchors(page_url, page):
        if href in seen:
            continue
        seen.add(href)
        tracks.append(
            PageTrack(audio_url=href, title=text or _title_from_url(href), source_url=href)
        )
    return PageExtraction(page_url, METHOD_LINKS, tracks[:MAX_TRACKS], "links")


def candidate_audio_urls(page_url: str, page: str) -> list[str]:
    """Every audio URL the page contains, anywhere: links, players, scripts, data attributes.

    What the AI path may choose from. A model that names a URL not in this list has
    invented it, and is not believed.
    """
    found: list[str] = []
    seen: set[str] = set()
    pattern = r"""(?:https?:)?//[^\s"'<>()\\]+?\.(?:mp3|ogg|oga|opus|wav|flac|m4a|aac)(?:\?[^\s"'<>()\\]*)?(?=["'\s<>)\\]|$)"""
    for match in re.finditer(pattern, html_lib.unescape(page), re.I):
        url = urljoin(page_url, match.group(0))
        if url not in seen:
            seen.add(url)
            found.append(url)
    for href, _text in _audio_anchors(page_url, page):
        if href not in seen:
            seen.add(href)
            found.append(href)
    return found


def page_text(page: str, limit: int = 60_000) -> str:
    """The page as readable text for a model, with every link's target kept inline.

    Scripts and styles go, except JSON-LD and inline JSON, which often hold exactly the
    track list a model needs. Link targets are kept as "text [link: url]" so the model can
    tie a title to its file; angle brackets would not survive the tag stripping below.
    """
    body = re.sub(r"<(style|noscript|svg)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    body = re.sub(r"<script(?![^>]*json)[^>]*>.*?</script>", " ", body, flags=re.S | re.I)
    body = re.sub(
        r"<a\b[^>]*href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        lambda m: f" {m.group(2)} [link: {m.group(1)}] ",
        body,
        flags=re.S | re.I,
    )
    body = re.sub(
        r"<(?:audio|source)\b[^>]*src\s*=\s*[\"']([^\"']+)[\"'][^>]*>",
        lambda m: f" [audio: {m.group(1)}] ",
        body,
        flags=re.I,
    )
    body = re.sub(r"<br\s*/?>|</(?:p|div|li|tr|h\d)>", "\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html_lib.unescape(body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body)
    return body.strip()[:limit]


def extract_saved(page: str) -> PageExtraction:
    """Tracks from a page the person saved in their own browser. Never touches the network.

    For sites that refuse automated requests (Pixabay answers a server with 403): the
    person opens the page, saves it, and downloads the files themselves, so the only
    thing GAM does with the site is read what they handed it. The page's own address is
    recovered from the saved file, since adapters key on the host and on the path.
    """
    page_url = saved_page_url(page) or ""
    host = (urlsplit(page_url).hostname or "").lower()

    if _is_host(host, "pixabay.com"):
        tracks = _pixabay(page_url, page)
        if tracks:
            return PageExtraction(page_url, METHOD_STRUCTURED, tracks[:MAX_TRACKS], "pixabay")

    try:
        found = extract_structured(page_url, page, _no_network)
    except SavedPageNeedsMore as exc:
        return PageExtraction(page_url, METHOD_STRUCTURED, [], "saved page", note=str(exc))
    if found is not None:
        return found
    links = extract_links(page_url, page)
    if not links.tracks:
        links.note = "No tracks were found in the saved page."
    return links


class SavedPageNeedsMore(Exception):
    """An adapter needed a second file the saved page only points to."""


def _no_network(url: str) -> tuple[str, str]:
    name = urlsplit(url).path.rsplit("/", 1)[-1] or url
    raise SavedPageNeedsMore(
        f"This page loads its track list from {name}. Use the page link instead of a saved copy."
    )


def saved_page_url(page: str) -> Optional[str]:
    """Where a saved page came from: its canonical link, its og:url, or the comment
    Chrome and Edge write at the top of a saved page."""
    for pattern in (
        r"<link\b[^>]*rel=[\"']canonical[\"'][^>]*href=[\"']([^\"']+)[\"']",
        r"<link\b[^>]*href=[\"']([^\"']+)[\"'][^>]*rel=[\"']canonical[\"']",
        r"<meta\b[^>]*property=[\"']og:url[\"'][^>]*content=[\"']([^\"']+)[\"']",
        r"<!--\s*saved from url=\(\d+\)(\S+?)\s*-->",
    ):
        match = re.search(pattern, page, re.I)
        if match and match.group(1).startswith(("http://", "https://")):
            return html_lib.unescape(match.group(1))
    return None


# ─── matching files to tracks ────────────────────────────────────────────────


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def match_files(tracks: list[PageTrack], filenames: list[str]) -> dict[str, Optional[PageTrack]]:
    """Which track each downloaded file is. One file per track, one track per file.

    By the site's id in the filename first (Pixabay names a download
    `artist-title-604094.mp3`), which cannot be wrong. Then by title contained in the
    filename, longest title first, so a file named for "Tension Riser" is not taken by
    the track called "Riser". A file matching nothing maps to None.
    """
    result: dict[str, Optional[PageTrack]] = {name: None for name in filenames}
    taken: set[int] = set()
    by_id = {t.match_id: i for i, t in enumerate(tracks) if t.match_id}

    for name in filenames:
        stem = name.rsplit(".", 1)[0]
        for number in re.findall(r"\d+", stem):
            index = by_id.get(number)
            if index is not None and index not in taken:
                result[name] = tracks[index]
                taken.add(index)
                break

    titled = sorted(
        ((i, _normalise(t.title)) for i, t in enumerate(tracks) if _normalise(t.title)),
        key=lambda pair: len(pair[1]),
        reverse=True,
    )
    for name in filenames:
        if result[name] is not None:
            continue
        stem = f" {_normalise(name.rsplit('.', 1)[0])} "
        for index, title in titled:
            if index not in taken and f" {title} " in stem:
                result[name] = tracks[index]
                taken.add(index)
                break
    return result


# ─── pixabay ─────────────────────────────────────────────────────────────────

# A saved copy of a Pixabay music listing, as the browser rendered it: one row per track.
# The class names carry build hashes (`audioRow--et3ac`) that change every release, so
# only their stable prefixes are matched. There is no audio URL in a row, since
# downloading is a button, so the person's own downloads are matched to these rows.
_PIXABAY_ROW = re.compile(r"class=\"audioRow--")
_PIXABAY_TITLE = re.compile(r"<a href=\"(/music/[^\"]+)\"[^>]*class=\"title--[^\"]*\"[^>]*>([^<]*)</a>")
_PIXABAY_ARTIST = re.compile(r"class=\"name--[^\"]*\"[^>]*>([^<]*)</a>")
_PIXABAY_DURATION = re.compile(r"class=\"duration--[^\"]*\"[^>]*>([^<]*)<")
_PIXABAY_GENRE = re.compile(r"class=\"theme--[^\"]*\"[^>]*>([^<]*)</a>")
_PIXABAY_TAGS = re.compile(r"class=\"tags--[^\"]*\"[^>]*>(.*?)</div>", re.S)
_PIXABAY_LICENSE = "Pixabay Content License (https://pixabay.com/service/license-summary/)"


def _pixabay(page_url: str, page: str) -> list[PageTrack]:
    starts = [m.start() for m in _PIXABAY_ROW.finditer(page)] + [len(page)]
    kind, label = _pixabay_page_filter(page_url)
    tracks: list[PageTrack] = []
    for start, end in zip(starts, starts[1:]):
        row = page[start:end]
        title_match = _PIXABAY_TITLE.search(row)
        if not title_match:
            continue
        link = html_lib.unescape(title_match.group(1))
        title = _plain(title_match.group(2), 255)
        if not title:
            continue
        artist = _PIXABAY_ARTIST.search(row)
        duration = _PIXABAY_DURATION.search(row)
        genre = _PIXABAY_GENRE.search(row)
        tag_block = _PIXABAY_TAGS.search(row)
        tags = (
            [html_lib.unescape(t) for t in re.findall(r">([^<]+)</a>", tag_block.group(1))]
            if tag_block
            else []
        )
        id_match = re.search(r"-(\d+)/?$", link)
        track = PageTrack(
            audio_url=None,
            title=title,
            creator=_plain(artist.group(1), MAX_TEXT) if artist else None,
            publisher="Pixabay",
            duration_seconds=_clock_seconds(duration.group(1)) if duration else None,
            license=_PIXABAY_LICENSE,
            source_url=urljoin("https://pixabay.com/", link),
            genres=music.clean_terms([html_lib.unescape(genre.group(1))]) if genre else [],
            tags=music.clean_terms(tags),
            match_id=id_match.group(1) if id_match else None,
        )
        if kind == "mood":
            track.moods = music.clean_terms([*track.moods, label])
        elif kind == "genre":
            track.genres = music.clean_terms([*track.genres, label])
        tracks.append(track)
    return tracks


def _pixabay_page_filter(page_url: str) -> tuple[Optional[str], str]:
    """`/music/search/mood/mysterious/` says every track listed is mysterious."""
    parts = [p for p in urlsplit(page_url).path.split("/") if p]
    if len(parts) >= 4 and parts[:2] == ["music", "search"] and parts[2] in ("mood", "genre"):
        return parts[2], unquote(parts[3]).replace("-", " ").title()
    return None, ""


# ─── incompetech ─────────────────────────────────────────────────────────────

# Kevin MacLeod's catalogue. The page renders nothing itself: its script fetches
# `pieces.json` beside it and builds the list in the browser. The genre and album names
# are lookup tables in that same script, read out of the page rather than copied here,
# so a genre added there is picked up without a change here.
#
# The credit is the attribution code the page shows under every track, verbatim: it is
# what the licence asks for, so it is stored as the credit line rather than composed.
_INCOMPETECH_CREATOR = "Kevin MacLeod"
_INCOMPETECH_PUBLISHER = "incompetech.com"
_INCOMPETECH_LICENSE = "CC BY 4.0"
_INCOMPETECH_CREDIT = (
    '"{title}" Kevin MacLeod (incompetech.com) '
    "Licensed under Creative Commons: By Attribution 4.0 License "
    "http://creativecommons.org/licenses/by/4.0/"
)
_INCOMPETECH_MP3_PATH = "/music/royalty-free/mp3-royaltyfree/"


def _incompetech(page_url: str, page: str, fetch_text: FetchText) -> list[PageTrack]:
    genres = {
        m.group(1): m.group(2)
        for m in re.finditer(r'"id"\s*:\s*"?(\d+)"?\s*,\s*"genre"\s*:\s*"([^"]+)"', page)
    }
    albums = {
        m.group(3): m.group(1)
        for m in re.finditer(
            r'"collection_name"\s*:\s*"([^"]+)"\s*,\s*"collection_category"\s*:\s*"([^"]+)"'
            r'\s*,\s*"code"\s*:\s*"(\d+)"',
            page,
        )
    }

    pieces_url, raw = fetch_text(urljoin(page_url, "pieces.json"))
    try:
        pieces = json.loads(raw)
    except ValueError:
        return []
    if isinstance(pieces, dict):
        pieces = pieces.get("pieces") or pieces.get("data") or []
    if not isinstance(pieces, list):
        return []

    tracks: list[PageTrack] = []
    for piece in pieces:
        if not isinstance(piece, dict):
            continue
        filename = _text(piece.get("filename"))
        title = _text(piece.get("title"))
        if not filename or not title or extension_of(filename) not in AUDIO_EXTENSIONS:
            continue
        audio_url = urljoin(pieces_url, _INCOMPETECH_MP3_PATH + quote(filename))
        genre = genres.get(str(piece.get("genre") or ""))
        album = albums.get(str(piece.get("collection") or ""))
        tracks.append(
            PageTrack(
                audio_url=audio_url,
                title=title[:255],
                creator=_INCOMPETECH_CREATOR,
                publisher=_INCOMPETECH_PUBLISHER,
                source_title=album if album and album != "not in a collection" else None,
                description=_plain(piece.get("description"), MAX_DESCRIPTION),
                duration_seconds=_clock_seconds(piece.get("length")),
                published_date=normalise_partial_date(_text(piece.get("uploaded"))),
                license=_INCOMPETECH_LICENSE,
                credit_line=_INCOMPETECH_CREDIT.format(title=title),
                source_url=audio_url,
                genres=[genre] if genre and genre != "Unclassifiable" else [],
                moods=music.split_terms(piece.get("feel")),
                instruments=music.split_terms(piece.get("instruments")),
                bpm=music.parse_bpm(piece.get("bpm")),
            )
        )
        if len(tracks) >= MAX_TRACKS:
            break
    return tracks


# ─── schema.org JSON-LD ──────────────────────────────────────────────────────


def _json_ld_tracks(page_url: str, page: str) -> list[PageTrack]:
    tracks: list[PageTrack] = []
    seen: set[str] = set()
    for block in re.findall(
        r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        page,
        flags=re.S | re.I,
    ):
        try:
            data = json.loads(html_lib.unescape(block.strip()))
        except ValueError:
            continue
        for node in _walk(data):
            if not _is_type(node, "MusicRecording", "AudioObject"):
                continue
            track = _from_recording(page_url, node)
            if track and track.audio_url not in seen:
                seen.add(track.audio_url)
                tracks.append(track)
    return tracks


def _walk(node: Any) -> Iterable[dict]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _is_type(node: dict, *names: str) -> bool:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(k in names for k in kinds if isinstance(k, str))


def _from_recording(page_url: str, node: dict) -> Optional[PageTrack]:
    audio = node.get("audio") if isinstance(node.get("audio"), dict) else {}
    candidates = [node.get("contentUrl"), audio.get("contentUrl"), node.get("url")]
    audio_url = next(
        (
            urljoin(page_url, c)
            for c in candidates
            if isinstance(c, str) and extension_of(urlsplit(c).path) in AUDIO_EXTENSIONS
        ),
        None,
    )
    title = _text(node.get("name"))
    if not audio_url or not title:
        return None

    # The track's own page when the data names one. Otherwise the file itself, not the
    # listing it was found on: a listing is shared by every track on it, and the source
    # URL is also what recognises a track already imported.
    page_link = node.get("url")
    track_page = (
        urljoin(page_url, page_link)
        if isinstance(page_link, str) and urljoin(page_url, page_link) != audio_url
        else audio_url
    )
    album = node.get("inAlbum")
    licence_url = _text(node.get("license"))
    notice = _text(node.get("copyrightNotice"))
    return PageTrack(
        audio_url=audio_url,
        title=title[:255],
        creator=_name_of(node.get("byArtist")) or _name_of(node.get("creator")),
        publisher=_name_of(node.get("publisher")),
        source_title=_name_of(album) if album else None,
        description=_plain(node.get("description"), MAX_DESCRIPTION),
        duration_seconds=_iso_duration(node.get("duration") or audio.get("duration")),
        published_date=normalise_partial_date(_text(node.get("datePublished"))),
        license=(f"{notice} ({licence_url})" if notice and licence_url else notice or licence_url)
        or None,
        source_url=track_page,
        genres=_terms(node.get("genre")),
        tags=_terms(node.get("keywords")),
    )


def _name_of(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return _text(value)[:MAX_TEXT] or None
    if isinstance(value, dict):
        name = value.get("name")
        return _text(name)[:MAX_TEXT] or None if isinstance(name, str) else None
    if isinstance(value, list):
        names = [n for n in (_name_of(v) for v in value) if n]
        return ", ".join(names)[:MAX_TEXT] or None
    return None


def _terms(value: Any) -> list[str]:
    if isinstance(value, list):
        return music.clean_terms(v for v in value if isinstance(v, str))
    if isinstance(value, str):
        return music.split_terms(value)
    return []


# ─── mixkit ──────────────────────────────────────────────────────────────────

# Each card is an audio player keyed by its MP3, followed by links to the track's genre,
# moods, instruments and tags. The JSON-LD has the genre only, so the rest comes from
# here, matched to the JSON-LD track by that same MP3 URL.
_MIXKIT_CARD = re.compile(r'data-audio-player-preview-url-value="([^"]+)"')
_MIXKIT_LINK = re.compile(r'class="meta-links__link"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>')


def _enrich_mixkit(page_url: str, page: str, tracks: list[PageTrack]) -> None:
    by_url = {t.audio_url: t for t in tracks}
    cards = list(_MIXKIT_CARD.finditer(page))
    for index, card in enumerate(cards):
        track = by_url.get(urljoin(page_url, html_lib.unescape(card.group(1))))
        if track is None:
            continue
        end = cards[index + 1].start() if index + 1 < len(cards) else len(page)
        for link in _MIXKIT_LINK.finditer(page, card.end(), end):
            kind, label = _mixkit_kind(link.group(1)), html_lib.unescape(link.group(2)).strip()
            if kind == "mood":
                track.moods = music.clean_terms([*track.moods, label])
            elif kind == "instrument":
                track.instruments = music.clean_terms([*track.instruments, label])
            elif kind == "tag":
                track.tags = music.clean_terms([*track.tags, label])
            elif kind == "genre":
                track.genres = music.clean_terms([*track.genres, label])

    # A listing filtered to one mood or instrument says so of every track on it.
    kind, label = _mixkit_page_filter(page_url)
    for track in tracks:
        track.publisher = track.publisher or "Mixkit"
        if kind == "mood":
            track.moods = music.clean_terms([*track.moods, label])
        elif kind == "instrument":
            track.instruments = music.clean_terms([*track.instruments, label])


def _mixkit_kind(href: str) -> Optional[str]:
    parts = [p for p in urlsplit(href).path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "free-stock-music" and parts[1] in ("mood", "instrument", "tag"):
        return parts[1]
    if len(parts) == 2 and parts[0] == "free-stock-music":
        return "genre"
    return None


def _mixkit_page_filter(page_url: str) -> tuple[Optional[str], str]:
    parts = [p for p in urlsplit(page_url).path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "free-stock-music" and parts[1] in ("mood", "instrument"):
        return parts[1], unquote(parts[2]).replace("-", " ").title()
    return None, ""


# ─── plain links ─────────────────────────────────────────────────────────────


def _audio_anchors(page_url: str, page: str) -> Iterable[tuple[str, str]]:
    for match in re.finditer(
        r"<(?:a\b[^>]*href|audio\b[^>]*src|source\b[^>]*src)\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)(?=<)",
        page,
        flags=re.S | re.I,
    ):
        href = urljoin(page_url, html_lib.unescape(match.group(1)))
        if extension_of(urlsplit(href).path) not in AUDIO_EXTENSIONS:
            continue
        text = _plain(match.group(2), MAX_TEXT) or ""
        yield href, text


def _title_from_url(url: str) -> str:
    stem = unquote(urlsplit(url).path.rsplit("/", 1)[-1]).rsplit(".", 1)[0]
    return re.sub(r"[-_]+", " ", stem).strip()[:255] or "Untitled track"


# ─── small parsers ───────────────────────────────────────────────────────────


def _text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _plain(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", value))
    text = " ".join(text.split())
    return text[:limit] or None


def _iso_duration(value: Any) -> Optional[float]:
    """ISO 8601 `PT1M53S` (schema.org's form) in seconds."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"P(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)", value.strip())
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _clock_seconds(value: Any) -> Optional[float]:
    """`h:mm:ss` or `m:ss` in seconds."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parts = [float(p) for p in value.strip().split(":")]
    except ValueError:
        return None
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return total or None


def _is_host(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)
