"""What `/api/assets/{id}/versions` returns."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class VersionRead(BaseModel):
    id: str
    asset_id: str
    # The standard size it was made as: the frame's short side in pixels.
    short_side: int
    width: Optional[int] = None
    height: Optional[int] = None
    size_bytes: int
    codec: Optional[str] = None
    mime_type: Optional[str] = None
    # Signed, like an asset's own `file_url`.
    file_url: Optional[str] = None
    created_at: datetime


class VersionCreate(BaseModel):
    # Checked against `enrichment/versions.py::SIZES` in the route rather than typed as
    # a Literal, so a bad value gets the house error shape instead of a bare 422.
    short_side: int
