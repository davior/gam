"""HTTP Range responses.

Starlette's FileResponse has no Range support, so a `<video src>` pointed at it can
only download the whole file — the browser cannot seek, and a scrub bar does nothing.
For a library built around jumping to a timestamp inside a ninety-minute interview,
that is the difference between working and not.

Adapted from gecko-notes' `media_files.py`, which solves the same problem for its
static mount. Rewritten against the storage layer rather than a filesystem path, so it
keeps working when the bytes are not local.
"""

from __future__ import annotations

import re
from typing import BinaryIO, Iterator, Optional, Tuple

from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse

# 64 KiB: large enough that streaming a film is not a syscall storm, small enough that
# a cancelled request stops promptly.
CHUNK_SIZE = 64 * 1024

_RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range(header: Optional[str], size: int) -> Optional[Tuple[int, int]]:
    """Interpret a Range header as an inclusive `(start, end)`.

    Returns None when there is no range to honour — no header, a unit other than
    bytes, or a malformed value — in which case the caller sends the whole body. Raises
    416 only for a syntactically valid range that cannot be satisfied, which is what
    the specification asks for.

    Only single ranges are supported. Multi-range requests are rare, require a
    multipart/byteranges body, and no browser needs them for media playback.
    """
    if not header:
        return None

    match = _RANGE_PATTERN.match(header.strip())
    if not match:
        return None

    first, last = match.group(1), match.group(2)

    if not first and not last:
        return None

    if not first:
        # "bytes=-500" — the final 500 bytes. Players use this to read a trailing
        # index (an MP4 moov atom at the end of the file, for instance).
        suffix = int(last)
        if suffix <= 0:
            raise _unsatisfiable(size)
        start = max(0, size - suffix)
        return start, size - 1

    start = int(first)
    end = int(last) if last else size - 1

    if start >= size or start > end:
        raise _unsatisfiable(size)

    # A range running past the end is clamped, not rejected: a player asking for more
    # than exists should get what exists.
    return start, min(end, size - 1)


def _unsatisfiable(size: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
        detail={"code": "range_not_satisfiable", "message": "Requested range is outside the file"},
        headers={"Content-Range": f"bytes */{size}"},
    )


def iter_slice(handle: BinaryIO, start: int, end: int) -> Iterator[bytes]:
    """Yield `[start, end]` inclusive, then close the handle."""
    try:
        handle.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = handle.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        handle.close()


def ranged_response(
    handle: BinaryIO,
    *,
    size: int,
    media_type: str,
    range_header: Optional[str],
    filename: Optional[str] = None,
    cache_seconds: int = 0,
) -> StreamingResponse:
    """A 200 or a 206, depending on what was asked for."""
    headers = {
        # Advertised on every response, including the 200. A player checks this before
        # it will offer seeking at all.
        "accept-ranges": "bytes",
        "content-length": str(size),
    }
    if cache_seconds:
        headers["cache-control"] = f"private, max-age={cache_seconds}"
    if filename:
        # Quoted and sanitised by the caller; only ever used for a download name.
        headers["content-disposition"] = f'inline; filename="{filename}"'

    requested = parse_range(range_header, size)
    if requested is None:
        return StreamingResponse(
            iter_slice(handle, 0, size - 1),
            status_code=status.HTTP_200_OK,
            media_type=media_type,
            headers=headers,
        )

    start, end = requested
    headers["content-length"] = str(end - start + 1)
    headers["content-range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        iter_slice(handle, start, end),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=media_type,
        headers=headers,
    )
