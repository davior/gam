"""Standard-resolution versions of a video: the 1080p, the 720p, beside the original.

A row per file rather than new `Asset` rows, because a version is not a separate thing
in the library. It has no name, tags, attribution or transcript of its own; it is the
same video at another size, so all of that stays on the one asset and nothing has to be
copied or kept in step. YouTube's model: the original, plus whichever sizes have been
made from it.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from app.clock import utcnow


class AssetVersion(SQLModel, table=True):
    # One file per size per asset. Making a size that exists is refused by the router
    # before it queues, and this is what makes that a guarantee rather than a check.
    __table_args__ = (UniqueConstraint("asset_id", "short_side", name="ux_assetversion_size"),)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    asset_id: str = Field(foreign_key="asset.id", index=True)
    user_id: str = Field(index=True)

    # The standard size this is, as the frame's short side in pixels: 1080 is 1920x1080
    # and 1080x1920 alike (see `enrichment/versions.py::SIZES`). The real dimensions
    # are below; this is the label it was asked for.
    short_side: int

    storage_key: str
    mime_type: Optional[str] = None
    size_bytes: int = Field(default=0)
    checksum_sha256: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    codec: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow)
