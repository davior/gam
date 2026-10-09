"""Fetching what fal made: streamed, capped, and checked at every hop.

The first streaming download in GAM — uploads arrive streamed, and yt-dlp does its own
fetching. gecko-notes reads the whole body into memory and checks its size afterwards,
which caps nothing: the cap is enforced here while the bytes arrive, against the
`Content-Length` first when there is one, because a video is one file and a check made
after it is written has already filled the disk.

The URL is fal's answer, not the user's, but it is still a URL this server fetches on
somebody's behalf, so it gets the same SSRF check a pasted link does — on every redirect
hop too, since a public URL that redirects inward is the classic way past a check made
only on the first. `safe_url`'s own docstring calls this defence in depth; DNS can still
change between the check and the connect.

No credentials go with it. fal's output URLs are public CDN links, and the fal key sent
to wherever a result happened to point would be a key handed to that host.

The body is asked for, and accepted, only uncompressed. httpx decodes gzip and brotli
transparently and with no output limit, so a few hundred bytes of brotli can become
gigabytes in memory before the first byte is counted — the cap would protect the disk
and not the worker. Media is already compressed; nothing is lost by refusing it.

A failure to fetch is retried a few times before it fails the job. By the time this
runs fal has finished, and billed, and keeps the file for days: one CDN hiccup should
not cost a second generation.
"""

from __future__ import annotations

import base64
import binascii
import logging
import mimetypes
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import HTTPException

from app.generation.errors import GenerationError
from app.ingest.filetypes import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, extension_of
from app.jobs.runner import readable_error
from app.safe_url import require_safe_external_url

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 3
# Per read. A CDN that sends nothing at all for a minute has stopped; one that keeps
# sending a trickle is caught by `max_seconds` instead, which covers the whole file.
TIMEOUT = httpx.Timeout(60.0, connect=10.0)
ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2.0
# Looked up at call time, so a test makes the wait between attempts instant.
sleep = time.sleep
monotonic = time.monotonic

MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# `received`, and `total` when the server said.
Progress = Callable[[int, Optional[int]], None]


def download(
    url: str,
    workdir: Path,
    stem: str,
    *,
    max_bytes: int,
    default_extension: str,
    content_type_hint: Optional[str] = None,
    on_progress: Optional[Progress] = None,
    max_seconds: Optional[float] = None,
) -> Path:
    """Fetch `url` into `workdir/<stem><ext>` and return the path.

    The extension decides the asset type on ingest, so it comes from the URL path when
    that names a media type, else from the response's `Content-Type` (then fal's own
    `content_type` for the file), else `default_extension`. `on_progress` is called
    between chunks; the job's progress callback raises there to cancel, which closes the
    stream and removes the partial file on the way out.
    """
    report = on_progress or (lambda _received, _total: None)

    if url.startswith("data:"):
        # `sync_mode` in a catalogue row's extra_params makes fal answer inline. Not
        # something the seeded rows ask for, but nothing about it is worth refusing.
        return _from_data_uri(
            url, workdir, stem, max_bytes=max_bytes, default_extension=default_extension
        )

    for attempt in range(1, ATTEMPTS + 1):
        try:
            return _fetch(
                url,
                workdir,
                stem,
                max_bytes=max_bytes,
                default_extension=default_extension,
                content_type_hint=content_type_hint,
                report=report,
                max_seconds=max_seconds,
            )
        except _Transient as exc:
            if attempt == ATTEMPTS:
                raise GenerationError(str(exc)) from exc
            logger.info("Download attempt %d failed (%s); trying again", attempt, exc)
            sleep(RETRY_DELAY_SECONDS * attempt)
    raise AssertionError("unreachable")  # pragma: no cover


class _Transient(GenerationError):
    """A failure that says nothing about the file: worth another attempt."""


def _fetch(
    url: str,
    workdir: Path,
    stem: str,
    *,
    max_bytes: int,
    default_extension: str,
    content_type_hint: Optional[str],
    report: Progress,
    max_seconds: Optional[float],
) -> Path:
    target = url
    headers = {"Accept-Encoding": "identity"}
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers=headers) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            _require_safe(target)
            try:
                with client.stream("GET", target) as response:
                    # `is_redirect` already requires a Location header.
                    if response.is_redirect:
                        target = urljoin(target, response.headers["location"])
                        continue
                    if response.status_code == 429 or response.status_code >= 500:
                        raise _Transient(
                            f"Could not download the generated file (HTTP {response.status_code})"
                        )
                    if response.status_code >= 400:
                        raise GenerationError(
                            f"Could not download the generated file (HTTP {response.status_code})"
                        )

                    # Asked for identity; a server that compresses anyway is refused
                    # rather than decoded — see the module docstring.
                    encoding = response.headers.get("content-encoding", "").strip().lower()
                    if encoding not in ("", "identity"):
                        raise GenerationError(
                            "Could not download the generated file (it was sent compressed)"
                        )

                    total = _content_length(response)
                    if total is not None and total > max_bytes:
                        raise _too_large(max_bytes)

                    extension = _extension_for(
                        target,
                        response.headers.get("content-type"),
                        content_type_hint,
                        default_extension,
                    )
                    destination = workdir / f"{stem}{extension}"
                    _stream_to(
                        response,
                        destination,
                        max_bytes=max_bytes,
                        total=total,
                        report=report,
                        max_seconds=max_seconds,
                    )
                    return destination
            except httpx.TimeoutException as exc:
                raise _Transient("The generated file did not download in time") from exc
            except httpx.RequestError as exc:
                # The type, not the text: a RequestError's message carries the full URL.
                raise _Transient(
                    f"Could not download the generated file ({type(exc).__name__})"
                ) from exc

    raise GenerationError("Could not download the generated file (too many redirects)")


def _require_safe(url: str) -> None:
    try:
        require_safe_external_url(url)
    except HTTPException as exc:
        raise GenerationError(
            f"The generated file is at an address this app will not fetch: {readable_error(exc)}"
        ) from exc


def _stream_to(
    response: httpx.Response,
    destination: Path,
    *,
    max_bytes: int,
    total: Optional[int],
    report: Progress,
    max_seconds: Optional[float],
) -> None:
    received = 0
    started = monotonic()
    try:
        with open(destination, "wb") as handle:
            # As each read arrives, unbuffered: a chunk per read means the deadline and
            # the cancel checkpoint in `report` are reached even when the server sends a
            # trickle that would never fill a buffer. Decoding is identity by now — any
            # other encoding was refused above.
            for chunk in response.iter_bytes():
                received += len(chunk)
                # Counted, not trusted: Content-Length may be absent, or wrong.
                if received > max_bytes:
                    raise _too_large(max_bytes)
                if max_seconds is not None and monotonic() - started > max_seconds:
                    raise GenerationError("The generated file did not download in time")
                handle.write(chunk)
                report(received, total)
    except BaseException:
        # BaseException, so a cancel raised from `report` cleans up too.
        destination.unlink(missing_ok=True)
        raise


def _from_data_uri(
    uri: str, workdir: Path, stem: str, *, max_bytes: int, default_extension: str
) -> Path:
    header, _, payload = uri.partition(",")
    if ";base64" not in header:
        raise GenerationError("fal.ai returned an inline file this app cannot read")
    # Four characters of base64 are three bytes; checked before decoding, so an
    # oversized one is refused without being held in memory twice.
    if len(payload) * 3 // 4 > max_bytes:
        raise _too_large(max_bytes)
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GenerationError("fal.ai returned an inline file this app cannot read") from exc

    media_type = header[len("data:"):].split(";", 1)[0]
    destination = workdir / f"{stem}{_extension_for('', media_type, None, default_extension)}"
    destination.write_bytes(data)
    return destination


def _too_large(max_bytes: int) -> GenerationError:
    return GenerationError(
        f"The generated file is larger than the {max_bytes // (1024 * 1024)} MB limit"
    )


def _content_length(response: httpx.Response) -> Optional[int]:
    raw = response.headers.get("content-length")
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def _extension_for(
    url: str, content_type: Optional[str], hint: Optional[str], default: str
) -> str:
    from_path = extension_of(urlsplit(url).path) if url else ""
    if from_path in MEDIA_EXTENSIONS:
        return from_path
    for candidate in (content_type, hint):
        media_type = (candidate or "").split(";", 1)[0].strip().lower()
        guessed = mimetypes.guess_extension(media_type) if media_type else None
        if guessed and guessed.lower() in MEDIA_EXTENSIONS:
            return guessed.lower()
    return default
