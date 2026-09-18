"""What kind of thing a file is, decided by its extension.

The taxonomy is gecko-notes' (`routers/media.py`), renamed to the singular asset types
this app's API speaks. Extension-based, like gecko-notes: content sniffing would be
stronger, but the allowlist is what actually bounds risk here — nothing is executed,
and a file that lies about its type simply fails to probe or thumbnail.
"""

from __future__ import annotations

import os
from typing import Optional

IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".tiff", ".tif",
    ".ico", ".heic", ".heif", ".svg",
})
VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg",
})
AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".ogg", ".wav", ".m4a", ".flac", ".aac", ".opus", ".wma", ".aiff",
})
DOCUMENT_EXTENSIONS = frozenset({
    ".pdf", ".txt", ".md", ".rtf", ".csv",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp",
})

ALLOWED_EXTENSIONS = (
    IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | DOCUMENT_EXTENSIONS
)

# Asset types, as the API and the UI speak them.
TYPE_IMAGE = "image"
TYPE_VIDEO = "video"
TYPE_AUDIO = "audio"
TYPE_DOCUMENT = "document"

ASSET_TYPES = frozenset({TYPE_IMAGE, TYPE_VIDEO, TYPE_AUDIO, TYPE_DOCUMENT})

# Sources, recording where an asset came from.
SOURCE_UPLOAD = "local_upload"
SOURCE_URL = "url"
SOURCE_AI = "ai_generated"
SOURCE_GVC = "gvc_export"
# M7. A clip owns no bytes of its own (`Asset.storage_key IS NULL`) — this is the
# frontend-facing signal for that fact, not the guard itself; `services/assets.py`'s
# delete guard checks `storage_key` directly, since that is what a delete actually
# depends on being true. Promoting a clip, or extracting one fresh from a parent,
# flips `source` to `SOURCE_SUBVIDEO` — a real, standalone file from here on.
SOURCE_CLIP = "clip"
SOURCE_SUBVIDEO = "sub_video"

# Pillow can decode these directly. The rest of the image set (HEIC, AVIF without a
# plugin, SVG) needs something else, so a thumbnail is skipped rather than attempted.
PILLOW_READABLE = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".ico",
})


def extension_of(filename: str) -> str:
    """Lowercased extension including the dot, or "" if there is none."""
    return os.path.splitext(filename)[1].lower()


def is_allowed(filename: str) -> bool:
    return extension_of(filename) in ALLOWED_EXTENSIONS


def asset_type_for(filename: str) -> Optional[str]:
    """The asset type, or None for anything outside the allowlist."""
    ext = extension_of(filename)
    if ext in IMAGE_EXTENSIONS:
        return TYPE_IMAGE
    if ext in VIDEO_EXTENSIONS:
        return TYPE_VIDEO
    if ext in AUDIO_EXTENSIONS:
        return TYPE_AUDIO
    if ext in DOCUMENT_EXTENSIONS:
        return TYPE_DOCUMENT
    return None


_UNSAFE_NAME_CHARS = str.maketrans({c: None for c in "\x00\r\n\\/"})


def sanitize_original_name(name: Optional[str]) -> Optional[str]:
    """Reduce an uploaded filename to something safe to store and display.

    It never reaches the filesystem — files are stored under a UUID — so this only
    guards the label shown in the UI, which is otherwise attacker-controlled text.
    """
    if not name:
        return None
    cleaned = os.path.basename(name).translate(_UNSAFE_NAME_CHARS).strip()
    # Control characters that are not newlines would still render oddly.
    cleaned = "".join(ch for ch in cleaned if ch.isprintable())
    return cleaned[:255] or None


def display_name_from(filename: Optional[str]) -> str:
    """A sensible default asset name: the filename without its extension.

    Ingestion requires only a file, so something has to fill the name field, and
    "beach_sunset.jpg" is a better starting point than a UUID.
    """
    cleaned = sanitize_original_name(filename)
    if not cleaned:
        return "Untitled"
    stem = os.path.splitext(cleaned)[0].strip()
    return stem[:255] or cleaned[:255]
