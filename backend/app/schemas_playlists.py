"""Request and response shapes for the playlist API.

An item reads back with everything a player (or Video Creator) needs without a second
request: the asset's kind, freshly signed URLs, and the stretch to play. `start`/`end`
are the range resolved against the asset (the whole file when the item has none), while
`in_point`/`out_point` are what is stored, so an editor can tell "the whole asset" from
"a range that happens to cover all of it".
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class PlaylistItemIn(BaseModel):
    asset_id: str = Field(min_length=1)
    in_point: Optional[float] = Field(default=None, ge=0)
    out_point: Optional[float] = Field(default=None, gt=0)
    image_seconds: Optional[float] = None
    label: Optional[str] = Field(default=None, max_length=255)
    note: Optional[str] = Field(default=None, max_length=500)


class PlaylistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5_000)
    items: List[PlaylistItemIn] = []


class PlaylistUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5_000)


class PlaylistItemsAdd(BaseModel):
    items: List[PlaylistItemIn] = Field(min_length=1)
    # Where to insert; the end when absent.
    position: Optional[int] = Field(default=None, ge=0)


class PlaylistItemUpdate(BaseModel):
    """Only the fields sent are changed (`model_fields_set`). A range is sent as a pair,
    and `null` for both makes the item the whole asset again."""

    in_point: Optional[float] = Field(default=None, ge=0)
    out_point: Optional[float] = Field(default=None, gt=0)
    image_seconds: Optional[float] = None
    label: Optional[str] = Field(default=None, max_length=255)
    note: Optional[str] = Field(default=None, max_length=500)


class PlaylistOrder(BaseModel):
    item_ids: List[str]


class PlaylistItemRead(BaseModel):
    id: str
    position: int
    asset_id: str
    asset_name: str
    asset_type: str
    in_point: Optional[float] = None
    out_point: Optional[float] = None
    # Resolved: where playback starts and stops in the file. `end` is None only when the
    # asset's length was never measured.
    start: float
    end: Optional[float] = None
    image_seconds: Optional[float] = None
    label: Optional[str] = None
    note: Optional[str] = None
    asset_duration_seconds: Optional[float] = None
    mime_type: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    # Signed and time-limited, like every asset URL: read the playlist again for fresh ones.
    file_url: Optional[str] = None
    thumb_url: Optional[str] = None
    missing: bool = False


class PlaylistRead(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    item_count: int = 0
    total_seconds: float = 0.0
    thumb_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class PlaylistDetail(PlaylistRead):
    items: List[PlaylistItemRead] = []
