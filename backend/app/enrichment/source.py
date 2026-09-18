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
from app.models.document import DocumentPage
from app.models.transcript import TranscriptSegment
from app.providers.base import Image
from app.storage.base import StorageError

logger = logging.getLogger(__name__)

# Where the material came from, so a prompt can say so and a test can assert on it.
FROM_TRANSCRIPT = "transcript"
FROM_IMAGE = "image"
FROM_POSTER = "poster"
FROM_DOCUMENT = "document"

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


def document_text(session: Session, asset: Asset) -> str:
    """The asset's extracted text as one block, in order.

    Ordered by `idx` for the same reason `transcript_text` is: nothing in SQL promises
    to read rows back the way they went in, and a summary built from shuffled pages is
    wrong in a way that is hard to notice.
    """
    pages = session.exec(
        select(DocumentPage)
        .where(DocumentPage.asset_id == asset.id)
        .order_by(col(DocumentPage.idx))
    ).all()
    return "\n\n".join(p.text.strip() for p in pages if (p.text or "").strip()).strip()


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


def gather(
    session: Session,
    asset: Asset,
    *,
    supports_images: bool,
    include_poster: bool = False,
) -> SourceMaterial:
    """Pick the best material this asset has, for the provider it will be sent to.

    `supports_images` is the caller's provider capability, not the asset's: an image is
    only source material if something can actually look at it, and a text-only provider
    handed one would fail inside its own deserializer rather than saying why.

    `include_poster` attaches a video's still frame *alongside* its transcript rather
    than instead of it. Only `describe` asks for this, and the reason is that without it
    `describe` and `summarize` read exactly the same bytes for a transcribed video and
    write two paragraphs of the same paragraph into different columns. A description is
    supposed to say what is *in* the thing — the frame is the only source for that — and
    a summary what it is *about*. The transcript stays primary either way; this adds to
    it, and does not reorder the precedence the milestone doc sets out.
    """
    if asset.storage_key is None and asset.parent_asset_id:
        # A clip (M7). It has no transcript of its own — only the parent does, for the
        # whole recording rather than this range — and it inherits the parent's
        # `thumb_key` (see `services/assets.py::create_clip`), so without this check
        # the poster-fallback branch below would silently "succeed" using a generic
        # frame instead of ever raising. That produces a description of the wrong
        # content rather than failing loudly, which is worse than refusing outright.
        raise NoSourceMaterial("This is a clip. Describe, summarise or tag the original asset instead.")

    text = transcript_text(session, asset)
    if text:
        truncated = len(text) > MAX_TRANSCRIPT_CHARS
        poster = _poster_image(asset) if (include_poster and supports_images) else None
        return SourceMaterial(
            kind=FROM_TRANSCRIPT,
            text=text[:MAX_TRANSCRIPT_CHARS] if truncated else text,
            truncated=truncated,
            images=[poster] if poster else [],
        )

    if asset.asset_type == TYPE_IMAGE and supports_images:
        image = _own_image(asset)
        if image:
            return SourceMaterial(kind=FROM_IMAGE, images=[image])

    # A document with extracted text. Deliberately **above** the poster branch, not down
    # with the old refusal: every PDF gets a first-page thumbnail at ingest, so a PDF
    # sent to a vision provider used to match the poster branch and be summarised from a
    # picture of its cover. That is the same mistake the transcript rule at the top of
    # this module exists to prevent, one format over — the words beat the one rendered
    # page, and the fallback only applies when there are no words.
    if asset.asset_type == TYPE_DOCUMENT:
        body = document_text(session, asset)
        if body:
            truncated = len(body) > MAX_TRANSCRIPT_CHARS
            return SourceMaterial(
                kind=FROM_DOCUMENT,
                text=body[:MAX_TRANSCRIPT_CHARS] if truncated else body,
                truncated=truncated,
            )

    # A video with no transcript: the poster frame is all there is. Deliberately after
    # the transcript branch, never instead of it.
    if supports_images:
        poster = _poster_image(asset)
        if poster:
            return SourceMaterial(kind=FROM_POSTER, images=[poster])

    if asset.asset_type == TYPE_DOCUMENT:
        # Reached when extraction has not run, or ran and found nothing — a scan holds
        # pictures of words rather than words. Says which, because the two have different
        # answers: run it, versus this file needs OCR.
        raise NoSourceMaterial(
            "No text has been read out of this document yet. Run Extract text on it "
            "first — and if that finds nothing, the file is a scan and needs OCR."
        )

    raise NoSourceMaterial(
        "There is nothing to read yet. Transcribe this asset first, or add a provider "
        "that can look at images."
    )
