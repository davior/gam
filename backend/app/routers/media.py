"""Serving asset bytes.

Unauthenticated by URL, authorised by signature. A `<video>` or `<img>` element cannot
send an Authorization header, so the alternatives are a public path (what gecko-notes
does, which makes every upload readable by anyone holding the UUID) or a signature in
the query string. The signature is bound to both the key and the expiry, so a URL
cannot be edited into another asset's or extended.
"""

from __future__ import annotations

import logging
import mimetypes
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Query, status

from app.auth import verify_media_signature
from app.media.ranged import ranged_response
from app.storage import StorageError, build_storage, validate_key

router = APIRouter()

logger = logging.getLogger(__name__)


@router.get("/{key:path}")
def serve_media(
    key: str,
    exp: int = Query(..., description="Signature expiry, seconds since the epoch"),
    sig: str = Query(..., description="URL signature"),
    range_header: Optional[str] = Header(default=None, alias="Range"),
):
    validate_key_or_404(key)

    if not verify_media_signature(key, exp, sig):
        # One response for a bad signature and an expired one. Distinguishing them
        # would tell an attacker which half they got right.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "invalid_signature", "message": "This link is invalid or has expired"},
        )

    storage = build_storage()
    stat = storage.stat(key)
    if not stat.exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "No such file"},
        )

    try:
        handle = storage.open(key)
    except StorageError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "No such file"},
        ) from None

    return ranged_response(
        handle,
        size=stat.size_bytes,
        media_type=mimetypes.guess_type(key)[0] or "application/octet-stream",
        range_header=range_header,
        # Bytes never change under a key, so a signed URL's lifetime is the only limit
        # worth having. Private, because the URL is a credential.
        cache_seconds=3600,
    )


def validate_key_or_404(key: str) -> None:
    """A malformed key is a 404, not a 400.

    It is the same answer an attacker gets for a key that simply does not exist, so
    probing for the shape of valid keys learns nothing.
    """
    try:
        validate_key(key)
    except StorageError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "No such file"},
        ) from None
