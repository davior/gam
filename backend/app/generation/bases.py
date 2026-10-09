"""A base image as the data URI fal is sent.

Data URIs rather than an upload to fal's CDN, because GAM's media is private and must
not become public to serve this: an upload lands on a CDN with no expiry (gecko-notes'
audio uploads do exactly that), and it is a second, separately versioned API fal has
already broken once. The price is request size, which is why each base is normalised
here before it is encoded:

- EXIF orientation applied, since a model reads pixels, not tags — a portrait phone
  photo would otherwise arrive on its side;
- the long edge capped (`generation_base_max_edge`), which no seeded model reads past;
- re-encoded as JPEG, or PNG when it really has transparency — an edit model told to
  "put this on a beach" needs to know where the subject ends;
- refused if it is still over `generation_max_data_uri_mb`.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.generation.errors import GenerationError

JPEG_QUALITY = 90


def data_uri(path: Path, *, max_edge: int, max_bytes: int) -> str:
    """Normalise the image at `path` and return it as a `data:` URI."""
    try:
        with Image.open(path) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, ValueError) as exc:
        raise GenerationError("A base image could not be read as a picture") from exc

    if max(image.size) > max_edge:
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    if _has_transparency(image):
        image.convert("RGBA").save(buffer, format="PNG", optimize=True)
        media_type = "image/png"
    else:
        image.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY)
        media_type = "image/jpeg"

    uri = f"data:{media_type};base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"
    if len(uri) > max_bytes:
        raise GenerationError(
            f"A base image is larger than the {max_bytes // (1024 * 1024)} MB limit "
            "even after resizing"
        )
    return uri


def _has_transparency(image: Image.Image) -> bool:
    """Whether any pixel is actually see-through, not merely whether it could be.

    Many PNGs carry an alpha channel that is opaque everywhere; sending those as PNG
    would cost several times the bytes of a JPEG for nothing.
    """
    if image.mode == "P":
        return "transparency" in image.info
    if image.mode not in ("RGBA", "LA", "PA"):
        return False
    low, _high = image.getchannel("A").getextrema()
    return low < 255
