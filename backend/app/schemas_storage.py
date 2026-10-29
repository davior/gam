"""What `/api/storage` returns."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class FileGroup(BaseModel):
    files: int = 0
    bytes: int = 0


class TypeUsage(BaseModel):
    asset_type: str
    files: int
    bytes: int


class LargestAsset(BaseModel):
    id: str
    name: str
    asset_type: str
    # The original's bytes alone, as the asset's own `size_bytes` reads.
    size_bytes: int
    # What its standard-resolution versions add on top. Ranked by the sum of the two,
    # since deleting the asset frees both.
    versions_bytes: int = 0


class LibraryUsage(BaseModel):
    """One user's share: what they could free by deleting things."""

    total_bytes: int
    files: int
    by_type: List[TypeUsage]
    # Standard-resolution versions (1080p, 720p, ...) of videos. Their own group rather
    # than folded into "video", so the panel can say how much is originals and how much
    # is copies that could be deleted without losing a video.
    versions: FileGroup = FileGroup()
    thumbnails: FileGroup
    largest: List[LargestAsset]


class VolumeRead(BaseModel):
    # media | database | working — every role GAM puts on this one filesystem.
    roles: List[str]
    total_bytes: int
    used_bytes: int
    free_bytes: int
    level: str
    # Whether this volume's level counts. The working volume alone does not; see
    # `storage/capacity.py::Volume.guarded`.
    guarded: bool


class ServerUsage(BaseModel):
    """Admin only. The whole server, every user included."""

    volumes: List[VolumeRead]
    # Every file under the media root: all users, thumbnails and strays included.
    media: FileGroup
    # None when the database is not a local SQLite file.
    database_bytes: Optional[int] = None
    stray: FileGroup
    warn_free_bytes: int
    reserve_bytes: int


class StorageSummary(BaseModel):
    # Everybody's: the header's level, so the panel can say why an upload was refused
    # without telling a non-admin how full the server is.
    level: str
    library: LibraryUsage
    server: Optional[ServerUsage] = None


class StorageStatus(BaseModel):
    level: str
    # Admin only; null for everyone else, who is told the level and not the numbers.
    free_bytes: Optional[int] = None
    total_bytes: Optional[int] = None


class CleanupResult(BaseModel):
    removed: FileGroup
    # Strays the sweep found and could not delete. Logged with the reason.
    skipped: int = 0
