"""Preview images.

gecko-notes thumbnails images only, so its Assets panel shows a generic icon for every
video, which is close to useless for a library whose point is recognising a clip at a
glance. Three sources here: images through Pillow, video through an ffmpeg frame grab,
and PDFs through pypdfium2.

Everything degrades. A thumbnail is a convenience; failing to make one must never fail
an upload, so every entry point returns None rather than raising.
"""

from __future__ import annotations

import io
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps, UnidentifiedImageError

from app.ingest.filetypes import (
    PILLOW_READABLE,
    TYPE_DOCUMENT,
    TYPE_IMAGE,
    TYPE_VIDEO,
    extension_of,
)
from app.media_tools import ffmpeg_available

logger = logging.getLogger(__name__)

# Bounding box, aspect preserved. 480 is gecko-notes' choice and holds up: sharp on a
# 2x grid tile without storing a second copy of the library.
MAX_DIMENSION = 480
JPEG_QUALITY = 82

FRAME_GRAB_TIMEOUT_SECONDS = 120


def generate(
    source: Path,
    asset_type: str,
    original_name: str = "",
    duration_seconds: Optional[float] = None,
) -> Optional[bytes]:
    """A JPEG preview for this file, or None if one cannot be made.

    JPEG for everything, deliberately: a preview never needs transparency, and one
    format means the UI never has to ask what it is about to display.

    `duration_seconds` comes from the probe that already ran, so the frame grab can
    pick a sensible point in the file rather than a fixed offset.
    """
    try:
        if asset_type == TYPE_IMAGE:
            return _from_image(source, original_name)
        if asset_type == TYPE_VIDEO:
            return _from_video(source, duration_seconds)
        if asset_type == TYPE_DOCUMENT and extension_of(original_name or source.name) == ".pdf":
            return _from_pdf(source)
    except Exception as exc:  # noqa: BLE001 - a preview must never fail an upload
        logger.warning("Thumbnail failed for %s: %s", source.name, exc)
    return None


# ─── images ──────────────────────────────────────────────────────────────────


def _from_image(source: Path, original_name: str) -> Optional[bytes]:
    if extension_of(original_name or source.name) not in PILLOW_READABLE:
        # HEIC, AVIF and SVG need decoders Pillow does not ship with. Skipping is
        # honest; a broken thumbnail would be worse than none.
        return None
    try:
        with Image.open(source) as image:
            # Phone photos carry orientation in EXIF rather than in the pixels, so
            # without this a portrait shot previews on its side.
            image = ImageOps.exif_transpose(image)
            return _encode(image)
    except (UnidentifiedImageError, OSError) as exc:
        logger.info("Pillow could not read %s: %s", source.name, exc)
        return None


# ─── video ───────────────────────────────────────────────────────────────────


def _from_video(source: Path, duration_seconds: Optional[float] = None) -> Optional[bytes]:
    """One frame, taken a little way in.

    Not frame zero: videos routinely open on black, a fade-in or a slate, and a grid of
    black rectangles identifies nothing. Seeking to roughly a tenth of the way in lands
    on real content for almost everything.
    """
    if not ffmpeg_available():
        return None

    seek = 1.0
    if duration_seconds and duration_seconds > 0:
        # Clamped: far enough in to clear a fade, never so far that a three-second clip
        # seeks past its own end.
        seek = min(max(duration_seconds * 0.1, 0.5), max(duration_seconds - 0.1, 0.0))

    with tempfile.TemporaryDirectory() as workdir:
        target = Path(workdir) / "frame.jpg"
        argv = [
            "ffmpeg",
            "-nostdin",
            # Before -i, so ffmpeg seeks rather than decoding everything up to the
            # mark. On a two-hour file that is the difference between instant and not.
            "-ss", f"{seek:.3f}",
            "-i", str(source),
            "-frames:v", "1",
            "-an",
            "-y",
            str(target),
        ]
        try:
            completed = subprocess.run(
                argv, capture_output=True, text=True, timeout=FRAME_GRAB_TIMEOUT_SECONDS
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Frame grab failed for %s: %s", source.name, exc)
            return None

        if completed.returncode != 0 or not target.is_file():
            # A seek past the end of a very short clip lands here; retry from the start
            # before giving up, since a first frame beats no preview at all.
            if seek > 0:
                return _from_video_at_start(source)
            logger.info("No frame extracted from %s", source.name)
            return None

        try:
            with Image.open(target) as image:
                return _encode(image)
        except (UnidentifiedImageError, OSError):
            return None


def _from_video_at_start(source: Path) -> Optional[bytes]:
    with tempfile.TemporaryDirectory() as workdir:
        target = Path(workdir) / "frame.jpg"
        try:
            completed = subprocess.run(
                ["ffmpeg", "-nostdin", "-i", str(source), "-frames:v", "1", "-an", "-y", str(target)],
                capture_output=True,
                text=True,
                timeout=FRAME_GRAB_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0 or not target.is_file():
            return None
        try:
            with Image.open(target) as image:
                return _encode(image)
        except (UnidentifiedImageError, OSError):
            return None


# ─── PDFs ────────────────────────────────────────────────────────────────────


def _from_pdf(source: Path) -> Optional[bytes]:
    """The first page.

    pypdfium2 rather than PyMuPDF: it is Apache/BSD licensed and ships self-contained
    wheels, where PyMuPDF is AGPL and would put obligations on anyone deploying this.
    It also avoids pdf2image's dependency on a poppler binary being installed.
    """
    try:
        import pypdfium2
    except ImportError:
        logger.info("pypdfium2 not installed; skipping PDF thumbnail")
        return None

    document = None
    try:
        document = pypdfium2.PdfDocument(source)
        if len(document) == 0:
            return None
        page = document[0]
        # Rendered at roughly twice the target so downscaling has detail to work with;
        # a page rendered at exactly 480px wide has unreadably thin text.
        bitmap = page.render(scale=2.0)
        image = bitmap.to_pil()
        return _encode(image)
    except Exception as exc:  # noqa: BLE001 - pdfium raises its own error types
        logger.info("Could not render PDF %s: %s", source.name, exc)
        return None
    finally:
        if document is not None:
            document.close()


# ─── shared encoding ─────────────────────────────────────────────────────────


def _encode(image: Image.Image) -> bytes:
    """Downscale to the bounding box and encode as JPEG."""
    # JPEG has no alpha channel, and a PNG or GIF with transparency would otherwise
    # raise here. Compositing onto white matches how the UI renders a tile.
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")

    image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue()
