"""The storage seam.

Every byte GAM holds goes through this interface. Phase 1 has one implementation
(local disk); the point of the indirection is that moving to object storage later is a
new class rather than a change at every call site.

`materialise()` is the method that makes that true. FFmpeg and Pillow need a real path
on a real filesystem, so any backend that is not a filesystem has to be able to produce
one — a local backend hands back the file it already has, and a remote one downloads to
a temp file. Without it, a later R2 backend would need every media call site rewritten.
"""

from __future__ import annotations

import posixpath
import re
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, BinaryIO, Protocol


class StorageError(Exception):
    """A key that is not addressable, or an object that is not there."""


@dataclass(frozen=True)
class StoredFile:
    key: str
    size_bytes: int
    # Computed while streaming, so it costs one pass rather than a second read. Phase 2
    # deduplication needs it, and it is the cheapest possible integrity check.
    sha256: str


@dataclass(frozen=True)
class FileStat:
    size_bytes: int
    exists: bool


# A key is "<owner>/<uuid><ext>" or "<owner>/<uuid>.thumb<ext>" — two segments, no
# traversal, no absolute paths. Validated on the way in and again on the way out,
# because this is the boundary between user-controlled text and the filesystem.
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}/[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")


def validate_key(key: str) -> str:
    """Return `key` if it is safely addressable, else raise.

    gecko-notes' equivalent is `parse_media_url`, and its docstring calls itself the
    thing standing between note content and `os.remove`. Same job here, and the same
    reason to keep it in one place: a traversal check that is written twice is written
    wrong once.
    """
    if not key or not _KEY_PATTERN.match(key):
        raise StorageError(f"Unsafe storage key: {key!r}")
    # Belt and braces. The pattern already excludes "/" inside a segment and any
    # segment starting with a dot, but normalising and comparing catches anything the
    # regex and the filesystem would disagree about.
    if posixpath.normpath(key) != key:
        raise StorageError(f"Unsafe storage key: {key!r}")
    return key


def new_key(owner: str, extension: str) -> str:
    """A fresh key for one owner.

    Files are stored under a UUID rather than their uploaded name: two people
    uploading `IMG_0001.jpg` must not collide, and an uploaded filename is
    attacker-controlled text that should never reach the filesystem. The original name
    is kept in the database, where it is only ever displayed.
    """
    ext = extension.lower()
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    if not re.fullmatch(r"(\.[A-Za-z0-9]{1,16})?", ext):
        raise StorageError(f"Unsafe file extension: {extension!r}")
    return validate_key(f"{owner}/{uuid.uuid4()}{ext}")


def thumb_key_for(key: str) -> str:
    """The sidecar key for a preview: `abc.mp4` -> `abc.thumb.jpg`.

    Derived rather than stored so a thumbnail can always be found (or cleaned up) from
    the asset's own key, even if the database row is gone.
    """
    validate_key(key)
    stem, _, _ = key.rpartition(".")
    return f"{stem or key}.thumb.jpg"


class Storage(Protocol):
    """What a storage backend must do."""

    async def write_stream(self, key: str, chunks: AsyncIterator[bytes]) -> StoredFile: ...

    def write_bytes(self, key: str, data: bytes) -> StoredFile: ...

    def write_file(self, key: str, source_path: Path) -> StoredFile:
        """For a file a job already produced on disk — ffmpeg's own output.

        Not a rename: `source_path` is typically under a `tempfile.TemporaryDirectory`,
        commonly a different filesystem from the storage root (a Docker bind mount,
        for instance), and `os.rename`/`os.replace` across filesystems raises `EXDEV`.
        Implementations copy the bytes in, the same way `write_stream` does.
        """
        ...

    def open(self, key: str) -> BinaryIO: ...

    def materialise(self, key: str) -> AbstractContextManager[Path]:
        """A real filesystem path for the object, valid inside the context."""
        ...

    def stat(self, key: str) -> FileStat: ...

    def delete(self, key: str) -> bool: ...
