"""What an enrichment job reads, chosen by what the asset actually is.

`docs/m6-ai-enrichment.md` ("What each job reads") explains why this is one step rather
than a decision each job makes for itself: the milestone's jobs are named for what they
produce, but the axis that varies is what they consume. Putting that in one place means
`describe` and `autotag` inherit it rather than re-deriving it, probably differently.

The rule that matters: **a transcribed video is summarised from its transcript, not from
its poster frame.** One frame of a two-hour interview shows a person sitting down; the
transcript says what was discussed, which is what the person searching is looking for.
The frame is the fallback for silent or untranscribed video, not the primary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session, col, select

from app.ingest.filetypes import TYPE_DOCUMENT, TYPE_IMAGE
from app.models.asset import Asset
from app.models.transcript import TranscriptSegment
from app.providers.base import Image
from app.storage.base import StorageError

logger = logging.getLogger(__name__)

# Where the material came from, so a prompt can say so and a test can assert on it.
FROM_TRANSCRIPT = "transcript"
FROM_IMAGE = "image"
FROM_POSTER = "poster"

# A transcript of a long interview is far more text than any model needs to summarise it,
# and the input side is what a long job costs. Cut at a generous ceiling rather than
# sending everything: the opening of a recording establishes what it is, which is the
# question being asked.
MAX_TRANSCRIPT_CHARS = 60_000


class NoSourceMaterial(Exception):
    """There is nothing to enrich this asset from.

    Not a failure of the provider or the job — the asset genuinely has no content a
    model could read yet, usually because a video has not been transcribed.
    """


@dataclass(frozen=True)
class SourceMaterial:
    kind: str
    text: str = ""
    images: list[Image] = field(default_factory=list)
    # True when `text` was cut at MAX_TRANSCRIPT_CHARS, so a prompt can say the
    # material is partial instead of implying it covered the whole recording.
    truncated: bool = False


def transcript_text(session: Session, asset: Asset) -> str:
    """The asset's transcript as one block of text, in order.

    Segment order is `idx`, not insertion order: `_store_segments` writes them in one
    go, but nothing in SQL promises to read them back the way they went in, and a
    summary built from shuffled dialogue is wrong in a way that is hard to notice.
    """
    segments = session.exec(
        select(TranscriptSegment)
        .where(TranscriptSegment.asset_id == asset.id)
        .order_by(col(TranscriptSegment.idx))
    ).all()
    return "\n".join(s.text.strip() for s in segments if (s.text or "").strip()).strip()


def _poster_image(asset: Asset) -> Optional[Image]:
    """The video poster ingest already generated, if it is still there.

    Best-effort: a missing thumbnail is a reason to fall through to whatever else the
    asset has, not to fail the job.
    """
    if not asset.thumb_key:
        return None

    from app.storage.local import build_storage

    try:
        with build_storage().open(asset.thumb_key) as handle:
            data = handle.read()
    except (StorageError, OSError):
        logger.info("Asset %s has a thumb_key but no readable thumbnail", asset.id)
        return None

    # thumbnails.py encodes every poster as JPEG, whatever the source format was.
    return Image(data=data, media_type="image/jpeg") if data else None


def _own_image(asset: Asset) -> Optional[Image]:
    if not asset.storage_key:
        return None

    from app.storage.local import build_storage

    try:
        with build_storage().open(asset.storage_key) as handle:
            data = handle.read()
    except (StorageError, OSError):
        return None

    return Image(data=data, media_type=asset.mime_type or "image/jpeg") if data else None


def gather(session: Session, asset: Asset, *, supports_images: bool) -> SourceMaterial:
    """Pick the best material this asset has, for the provider it will be sent to.

    `supports_images` is the caller's provider capability, not the asset's: an image is
    only source material if something can actually look at it, and a text-only provider
    handed one would fail inside its own deserializer rather than saying why.
    """
    text = transcript_text(session, asset)
    if text:
        truncated = len(text) > MAX_TRANSCRIPT_CHARS
        return SourceMaterial(
            kind=FROM_TRANSCRIPT,
            text=text[:MAX_TRANSCRIPT_CHARS] if truncated else text,
            truncated=truncated,
        )

    if asset.asset_type == TYPE_IMAGE and supports_images:
        image = _own_image(asset)
        if image:
            return SourceMaterial(kind=FROM_IMAGE, images=[image])

    # A video with no transcript: the poster frame is all there is. Deliberately after
    # the transcript branch, never instead of it.
    if supports_images:
        poster = _poster_image(asset)
        if poster:
            return SourceMaterial(kind=FROM_POSTER, images=[poster])

    if asset.asset_type == TYPE_DOCUMENT:
        # `extract_text` is specified in plan-of-attack and has no KIND_ constant and no
        # module, so a document carries no readable text yet. Named explicitly so this
        # reads as a known gap rather than as an asset that mysteriously cannot be
        # enriched.
        raise NoSourceMaterial(
            "Reading text out of documents is not built yet, so there is nothing to "
            "summarise for this file."
        )

    raise NoSourceMaterial(
        "There is nothing to read yet. Transcribe this asset first, or add a provider "
        "that can look at images."
    )
