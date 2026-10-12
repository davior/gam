"""Local-filesystem storage.

Layout is `MEDIA_DIR/<owner>/<uuid><ext>`, the same shape gecko-notes uses, so the
existing restic backup strategy applies unchanged: write-once UUID names mean an
incremental backup sends only what is new, with no dependence on timestamps.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import AsyncIterator, BinaryIO, Iterator

from app.storage.base import FileStat, StorageError, StoredFile, validate_key

logger = logging.getLogger(__name__)

# Big enough that a large upload is not a million syscalls, small enough that memory
# stays flat regardless of file size.
CHUNK_SIZE = 1024 * 1024


class LocalStorage:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # ─── paths ───────────────────────────────────────────────────────────────

    def _path(self, key: str) -> Path:
        validate_key(key)
        path = (self.root / key).resolve()
        root = self.root.resolve()
        # validate_key already rejects traversal, but a symlink under the media root
        # could still point outside it, and that check has to happen after resolving.
        if not path.is_relative_to(root):
            raise StorageError(f"Key escapes the storage root: {key!r}")
        return path

    # ─── writing ─────────────────────────────────────────────────────────────

    async def write_stream(self, key: str, chunks: AsyncIterator[bytes]) -> StoredFile:
        """Stream to disk, hashing as it goes.

        Written to a `.partial` file and renamed on success, so an interrupted upload
        leaves nothing that looks like a complete object. The rename is atomic within a
        filesystem, which is what makes "the key exists" mean "the bytes are all there".
        """
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".partial")

        digest = hashlib.sha256()
        size = 0
        try:
            with open(partial, "wb") as handle:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            os.replace(partial, path)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

        return StoredFile(key=key, size_bytes=size, sha256=digest.hexdigest())

    def write_bytes(self, key: str, data: bytes) -> StoredFile:
        """For content produced in-process — thumbnails, extracted frames."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".partial")
        try:
            partial.write_bytes(data)
            os.replace(partial, path)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        return StoredFile(key=key, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    def write_file(self, key: str, source_path: Path) -> StoredFile:
        """For a file a job already produced on disk — ffmpeg's sub-video output.

        A real copy, not `shutil.move`/`os.rename`: `source_path` usually lives under a
        `tempfile.TemporaryDirectory`, which is not guaranteed to share a filesystem
        with the storage root (it commonly does not, under a Docker bind mount), and a
        cross-filesystem rename raises `EXDEV`. Copies in `CHUNK_SIZE` pieces so a large
        video does not sit in memory whole, hashing as it streams — same shape as
        `write_stream`, reading from a file instead of an async iterator. The final
        `.partial` -> `path` rename is always same-filesystem, since both are under
        `self.root`.
        """
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".partial")

        digest = hashlib.sha256()
        size = 0
        try:
            with open(source_path, "rb") as src, open(partial, "wb") as dst:
                while chunk := src.read(CHUNK_SIZE):
                    dst.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            os.replace(partial, path)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

        return StoredFile(key=key, size_bytes=size, sha256=digest.hexdigest())

    # ─── reading ─────────────────────────────────────────────────────────────

    def open(self, key: str) -> BinaryIO:
        path = self._path(key)
        if not path.is_file():
            raise StorageError(f"No such object: {key!r}")
        return open(path, "rb")

    @contextmanager
    def materialise(self, key: str) -> Iterator[Path]:
        """The object's real path.

        Free here, because the object already is a file. A future object-storage
        backend downloads to a temp file and deletes it on exit — which is why callers
        must treat this as a context manager and not keep the path.
        """
        path = self._path(key)
        if not path.is_file():
            raise StorageError(f"No such object: {key!r}")
        yield path

    def stat(self, key: str) -> FileStat:
        path = self._path(key)
        try:
            return FileStat(size_bytes=path.stat().st_size, exists=path.is_file())
        except OSError:
            return FileStat(size_bytes=0, exists=False)

    # ─── deleting ────────────────────────────────────────────────────────────

    def delete(self, key: str) -> bool:
        """Remove an object. Returns whether anything was there.

        Never raises for a missing object: deletion is called from cleanup paths where
        "already gone" is success, not an error.
        """
        try:
            path = self._path(key)
        except StorageError:
            logger.warning("Refused to delete unsafe key %r", key)
            return False
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            logger.warning("Could not delete %s: %s", key, exc)
            return False

    def files(self, owner: str | None = None) -> Iterator[tuple[str, os.stat_result]]:
        """Every file in the layout, as `(key, stat)` — one owner's, or everyone's.

        Exactly two levels deep, `<owner>/<name>`, because that is the only shape a key
        can have. Anything else under the root — a nested directory, a stray file at the
        top — is not GAM's, so it is neither counted nor offered for removal. Symlinks
        are not followed: one could point anywhere, and `_path` would refuse it anyway.
        """
        if not self.root.is_dir():
            return
        if owner is not None:
            # The rule a key's first segment is held to, so an owner cannot name `..`.
            # One that fails it cannot have been written under, so it has no files.
            try:
                validate_key(f"{owner}/x")
            except StorageError:
                return
            owner_dirs = [self.root / owner]
        else:
            with os.scandir(self.root) as entries:
                owner_dirs = [Path(e.path) for e in entries if e.is_dir(follow_symlinks=False)]

        for owner_path in owner_dirs:
            if owner_path.is_symlink() or not owner_path.is_dir():
                continue
            with os.scandir(owner_path) as entries:
                for entry in entries:
                    if entry.is_file(follow_symlinks=False):
                        yield f"{owner_path.name}/{entry.name}", entry.stat(follow_symlinks=False)

    def usage_bytes(self, owner: str) -> int:
        """Total bytes held for one owner, thumbnails included."""
        owner_dir = self.root / owner
        if not owner_dir.is_dir():
            return 0
        return sum(f.stat().st_size for f in owner_dir.iterdir() if f.is_file())


def build_storage() -> LocalStorage:
    """The app's storage backend.

    One function, so swapping in another backend is one edit here rather than a search
    for every constructor call.
    """
    from app.config import settings

    return LocalStorage(settings.resolved_media_dir)
