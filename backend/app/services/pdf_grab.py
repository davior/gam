"""Rendering one page of a PDF to a picture, filed in the library as an ordinary image.

A PDF is a `document` asset: it cannot go in a playlist and Video Creator cannot overlay it.
A page rendered to a picture is an `image`, which both already handle, so this is the one
step between "the table on page 12" and "shown over a video".

Synchronous, like `image_import`: pypdfium2 renders a page in well under a second and the
caller is waiting for the asset to place. No new dependency, since the same library
already makes the first-page thumbnails (`ingest/thumbnails.py` records why it was chosen).
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Optional

from sqlmodel import Session

from app.ingest.filetypes import SOURCE_PDF_PAGE, TYPE_DOCUMENT
from app.models.asset import Asset
from app.services import assets as asset_service
from app.storage import LocalStorage, capacity

DEFAULT_SCALE = 2.0  # 144 dpi: sharp at 1080p, small enough to be a quick render
MIN_SCALE = 0.5
MAX_SCALE = 4.0
# A poster-sized page at the top scale would otherwise be a multi-hundred-megapixel
# bitmap held in memory, so the longest side is capped and the scale lowered to fit.
MAX_SIDE_PIXELS = 4096


class GrabError(Exception):
    """The page could not be grabbed. Worded for the person."""


def is_pdf(asset: Asset) -> bool:
    return (
        asset.asset_type == TYPE_DOCUMENT
        and (asset.file_format or "").lower() == "pdf"
        and bool(asset.storage_key)
    )


def page_count(storage: LocalStorage, asset: Asset) -> int:
    """How many pages the file has, read from the file: no column holds it."""
    import pypdfium2

    if not asset.storage_key:
        return 0
    with storage.materialise(asset.storage_key) as path:
        document = pypdfium2.PdfDocument(str(path))
        try:
            return len(document)
        finally:
            document.close()


def grab_problem(storage: LocalStorage, asset: Asset, page: int) -> Optional[str]:
    """Why this page cannot be grabbed, as a sentence, or None.

    Asked as a question, like `assets.cut_problem`, so the assistant can check a proposal
    and then check it again when the person applies it.
    """
    if not is_pdf(asset):
        return "Only a PDF can have its pages grabbed as pictures."
    if page < 1:
        return "Pages are counted from 1."
    try:
        total = page_count(storage, asset)
    except Exception:  # noqa: BLE001 - a damaged or password-protected file is an answer, not a crash
        return "This PDF could not be opened, so it has no pages to grab."
    if page > total:
        noun = "page" if total == 1 else "pages"
        return f"This PDF has {total} {noun}, so there is no page {page}."
    return None


def _scale_for(width_pt: float, height_pt: float, scale: float) -> float:
    longest = max(width_pt, height_pt) or 1.0
    return max(MIN_SCALE, min(scale, MAX_SIDE_PIXELS / longest))


def grab_page(
    session: Session,
    storage: LocalStorage,
    pdf: Asset,
    page: int,
    *,
    scale: float = DEFAULT_SCALE,
    name: Optional[str] = None,
) -> Asset:
    """Render `page` (1-based) of `pdf` and catalogue the picture as a child image.

    Raises `GrabError` with a sentence for anything the person can act on.
    """
    import pypdfium2

    problem = grab_problem(storage, pdf, page)
    if problem:
        raise GrabError(problem)
    if not capacity.has_room():
        raise GrabError(capacity.DISK_FULL_MESSAGE)
    scale = max(MIN_SCALE, min(float(scale), MAX_SCALE))

    label = (name or "").strip()[:255] or f"{pdf.name} p.{page}"
    stem = re.sub(r"[\\/]+", "-", label).strip()[:200] or "page"

    with tempfile.TemporaryDirectory(prefix="gam-grab-") as workdir:
        target = Path(workdir) / f"{stem}.png"
        with storage.materialise(pdf.storage_key) as source_path:  # type: ignore[arg-type]
            document = pypdfium2.PdfDocument(str(source_path))
            try:
                pdf_page = document[page - 1]
                try:
                    width, height = pdf_page.get_size()
                    image = pdf_page.render(scale=_scale_for(width, height, scale)).to_pil()
                finally:
                    pdf_page.close()
            finally:
                document.close()
        # PNG, not the JPEG the thumbnail uses: a table or a slide is flat colour and
        # sharp edges, which JPEG smears, and this is the file that goes on a video.
        image.convert("RGB").save(target, format="PNG", optimize=True)

        try:
            return asset_service.ingest_file(
                session,
                storage,
                user_id=pdf.user_id,
                source_path=target,
                filename=target.name,
                name=label,
                source=SOURCE_PDF_PAGE,
                parent_asset_id=pdf.id,
                source_page=page,
            )
        except asset_service.UnsupportedFile as exc:  # pragma: no cover - .png is always supported
            raise GrabError("The page could not be filed in the library.") from exc
