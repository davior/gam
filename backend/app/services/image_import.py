"""Importing one picture from the web, by its URL: what Video Creator files a picture
found by its image search through.

Synchronous, unlike every other import, because the caller is waiting to place the
picture on a scene and a picture is one bounded request: at most `web.MAX_IMAGE_BYTES`,
no transcoding, a thumbnail. A video or a track is minutes of download and goes on the
queue; this would spend longer queued than fetching.

What the search said about the picture is all there is to say. No backend knows who
made it or under what licence, so neither is written: an empty licence is the honest
"unknown" the attribution filters (`unattributed=true`) already find, and filling it
with a guess is the confidently wrong citation M10 exists to prevent.
"""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from app.clock import utcnow
from app.enrichment.page_import import same_file
from app.ingest import web
from app.ingest.filetypes import SOURCE_URL
from app.models.asset import Asset
from app.services import assets as asset_service
from app.services import tags as tag_service
from app.storage import build_storage, capacity

logger = logging.getLogger(__name__)

MAX_NAME = 255
MAX_DESCRIPTION = 20_000


class ImageImportError(Exception):
    """The picture could not be imported. Worded for the person."""


@dataclass
class ImageImport:
    image_url: str
    # The page the picture is on, when the search said. It is what the credit names.
    page_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    tags: list[str] = field(default_factory=list)


@dataclass
class ImportedImage:
    asset: Asset
    # True when the library already held it (same address, or the same bytes).
    existing: bool


def _by_address(session: Session, user_id: str, request: ImageImport) -> Optional[Asset]:
    # Only the picture's own address identifies it: a page holds many pictures, so a
    # page URL match would hand back a different one. `source_url` holds the picture's
    # address only when no page was known, so this catches those; a picture recorded
    # under its page is caught after the download instead, by its checksum.
    return session.exec(
        select(Asset).where(
            Asset.user_id == user_id,
            Asset.source_url == request.image_url,
            Asset.asset_type == "image",
        )
    ).first()


def _name(request: ImageImport, path: Path) -> str:
    for candidate in (request.title, request.description):
        text = " ".join((candidate or "").split())
        if text:
            return text[:MAX_NAME]
    return path.name


def import_image(session: Session, user_id: str, request: ImageImport) -> ImportedImage:
    web.require_safe(request.image_url)
    if request.page_url:
        # Never fetched, but stored and shown as a link, so it has to be a web address.
        if not request.page_url.startswith(("http://", "https://")):
            raise ImageImportError("The page address is not a web address")

    found = _by_address(session, user_id, request)
    if found is not None:
        return ImportedImage(found, existing=True)
    if not capacity.has_room():
        raise ImageImportError(capacity.DISK_FULL_MESSAGE)

    with tempfile.TemporaryDirectory(prefix="gam-image-") as workdir:
        try:
            path = web.download_image(request.image_url, Path(workdir), "picture")
        except web.WebFetchError as exc:
            raise ImageImportError(str(exc)) from exc

        duplicate = same_file(session, user_id, path)
        if duplicate is not None:
            # The same bytes from another address (a CDN size variant, a mirror), or a
            # picture uploaded by hand. Its details are a person's, so they stay.
            return ImportedImage(duplicate, existing=True)

        name = _name(request, path)
        stem = re.sub(r"[\\/]+", "-", name).strip()[:200] or "picture"
        try:
            asset = asset_service.ingest_file(
                session,
                build_storage(),
                user_id=user_id,
                source_path=path,
                filename=f"{stem}{path.suffix.lower()}",
                name=name,
                source=SOURCE_URL,
            )
        except asset_service.UnsupportedFile as exc:
            raise ImageImportError("The site sent a file the library does not accept") from exc

    _record_source(session, asset, request)
    return ImportedImage(asset, existing=False)


def _record_source(session: Session, asset: Asset, request: ImageImport) -> None:
    """Where it came from, stamped `embedded`: what the source said, unchecked."""
    changes: dict = {"source_url": request.page_url or request.image_url, "retrieved_at": utcnow()}
    description = " ".join((request.description or "").split())
    if description:
        changes["description"] = description[:MAX_DESCRIPTION]
    try:
        asset_service.apply_embedded_metadata(session, asset, changes)
    except Exception:  # noqa: BLE001 - the picture is in; losing its source must not undo that
        logger.warning("Could not record where %s came from", asset.id, exc_info=True)

    try:
        attached = 0
        for name in request.tags:
            tag = tag_service.get_or_create(session, asset.user_id, name)
            if tag is not None and tag_service.attach(session, asset.id, tag.id):
                attached += 1
        if attached:
            asset_service.reindex_ids(session, [asset.id])
    except Exception:  # noqa: BLE001 - same
        logger.warning("Could not tag %s", asset.id, exc_info=True)
