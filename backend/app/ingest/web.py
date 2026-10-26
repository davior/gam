"""Fetching a web page, or one audio file named on it, on the user's behalf.

For importing tracks from a page (`ingest/page_tracks.py`). The same rules as
`generation/download.py`, which is fal-specific (its errors, its retry policy, image and
video only), restated here for a page and a track rather than shared through a flag:

- **SSRF, on every hop.** The URL is the user's, and the track URLs are the page's. A
  public address that redirects inward is the classic way past a check made only on the
  first, so redirects are followed by hand and each target is checked.
- **Capped while it arrives,** against `Content-Length` first when there is one, then
  counted, because a check made after the write has already filled the disk.
- **Uncompressed bodies only.** httpx decodes gzip and brotli with no output limit, so a
  small compressed body could become gigabytes in memory before the cap saw a byte. A
  page is the exception, being small and almost always sent compressed: it is decoded,
  but read through a cap on the decoded size.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import HTTPException

from app.ingest.filetypes import AUDIO_EXTENSIONS, extension_of
from app.jobs.runner import readable_error
from app.safe_url import require_safe_external_url

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# A listing page with a hundred tracks and their markup is a few hundred kilobytes, and
# incompetech's whole catalogue as JSON about one megabyte.
MAX_PAGE_BYTES = 8 * 1024 * 1024
# A long, high-bitrate track. Anything bigger is not a soundtrack.
MAX_TRACK_BYTES = 200 * 1024 * 1024
MAX_TRACK_SECONDS = 15 * 60

# Says what is asking, so a site that wants to can tell this apart from a browser and
# from a crawler. It is one person's library fetching the tracks they picked.
USER_AGENT = "GeckoAssetManager/1.0 (+personal media library; one request per chosen track)"

monotonic = time.monotonic

# Bytes received, and the total when the server said.
Progress = Callable[[int, Optional[int]], None]

_AUDIO_TYPES = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/aac": ".aac",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
}


class WebFetchError(Exception):
    """A page or a track could not be fetched. The message is worded for the user."""


def require_safe(url: str) -> None:
    try:
        require_safe_external_url(url)
    except HTTPException as exc:
        raise WebFetchError(f"That address is not one this app will fetch: {readable_error(exc)}") from exc


def _client(accept_encoding: str) -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": accept_encoding},
    )


@contextmanager
def _get(client: httpx.Client, url: str) -> Iterator[tuple[str, httpx.Response]]:
    """Follow redirects by hand, checking each hop. Yields (final url, open response)."""
    target = url
    for _hop in range(MAX_REDIRECTS + 1):
        require_safe(target)
        with client.stream("GET", target) as response:
            if response.is_redirect:
                target = urljoin(target, response.headers["location"])
                continue
            yield target, response
            return
    raise WebFetchError("The address redirected too many times")


def _status_error(what: str, response: httpx.Response) -> WebFetchError:
    if response.status_code in (401, 403):
        return WebFetchError(f"The site refused to send the {what} (HTTP {response.status_code})")
    if response.status_code == 404:
        return WebFetchError(f"The site has no such {what} (HTTP 404)")
    return WebFetchError(f"Could not fetch the {what} (HTTP {response.status_code})")


def _content_length(response: httpx.Response) -> Optional[int]:
    raw = response.headers.get("content-length")
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def fetch_text(url: str, *, max_bytes: int = MAX_PAGE_BYTES) -> tuple[str, str]:
    """A page (or a JSON file) as text. Returns (the final URL after redirects, text).

    The final URL matters: relative links on the page resolve against it, not against
    what was typed.
    """
    try:
        with _client("gzip, deflate") as client, _get(client, url) as (final, response):
            if response.status_code >= 400:
                raise _status_error("page", response)
            chunks: list[bytes] = []
            received = 0
            # iter_bytes decodes as it goes, so this counts what the page becomes, not
            # what was sent: the cap holds against a compression bomb too.
            for chunk in response.iter_bytes():
                received += len(chunk)
                if received > max_bytes:
                    raise WebFetchError("The page is too large to read")
                chunks.append(chunk)
            encoding = response.encoding or "utf-8"
    except httpx.TimeoutException as exc:
        raise WebFetchError("The site took too long to answer") from exc
    except httpx.RequestError as exc:
        # The type, not the text: a RequestError's message carries the full URL.
        raise WebFetchError(f"Could not reach the site ({type(exc).__name__})") from exc

    return final, b"".join(chunks).decode(encoding, errors="replace")


def download_audio(
    url: str,
    workdir: Path,
    stem: str,
    *,
    max_bytes: int = MAX_TRACK_BYTES,
    on_progress: Optional[Progress] = None,
) -> Path:
    """Fetch one track into `workdir/<stem><ext>` and return the path.

    The extension comes from the URL path when it names an audio type, else from the
    response's `Content-Type`. Anything that is neither is refused: a link that answers
    with an HTML page (a login wall, a "download starting" interstitial) must not land in
    the library as a broken track.
    """
    report = on_progress or (lambda _received, _total: None)
    try:
        with _client("identity") as client, _get(client, url) as (final, response):
            if response.status_code >= 400:
                raise _status_error("track", response)
            encoding = response.headers.get("content-encoding", "").strip().lower()
            if encoding not in ("", "identity"):
                raise WebFetchError("The site sent the track compressed, which is refused")

            extension = _audio_extension(final, response.headers.get("content-type"))
            if extension is None:
                raise WebFetchError("That link is not an audio file")

            total = _content_length(response)
            if total is not None and total > max_bytes:
                raise _too_large(max_bytes)

            destination = workdir / f"{stem}{extension}"
            _stream_to(response, destination, max_bytes=max_bytes, total=total, report=report)
            return destination
    except httpx.TimeoutException as exc:
        raise WebFetchError("The track did not download in time") from exc
    except httpx.RequestError as exc:
        raise WebFetchError(f"Could not download the track ({type(exc).__name__})") from exc


def _audio_extension(url: str, content_type: Optional[str]) -> Optional[str]:
    from_path = extension_of(urlsplit(url).path)
    if from_path in AUDIO_EXTENSIONS:
        return from_path
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    return _AUDIO_TYPES.get(media_type)


def _stream_to(
    response: httpx.Response, destination: Path, *, max_bytes: int, total: Optional[int], report: Progress
) -> None:
    received = 0
    started = monotonic()
    try:
        with open(destination, "wb") as handle:
            for chunk in response.iter_bytes():
                received += len(chunk)
                if received > max_bytes:
                    raise _too_large(max_bytes)
                if monotonic() - started > MAX_TRACK_SECONDS:
                    raise WebFetchError("The track did not download in time")
                handle.write(chunk)
                report(received, total)
    except BaseException:
        # BaseException, so a cancel raised from `report` cleans up too.
        destination.unlink(missing_ok=True)
        raise


def _too_large(max_bytes: int) -> WebFetchError:
    return WebFetchError(f"The track is larger than the {max_bytes // (1024 * 1024)} MB limit")
