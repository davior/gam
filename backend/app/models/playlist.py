"""Playlists: named, ordered runs of assets and moments of them, played one after another.

An item is an asset plus an optional range, not a clip row. That was the person's call:
a playlist built from twenty moments of ten interviews should not leave twenty clips
behind in the library, and its ranges must stay editable from the playlist itself. The
shape is deliberately the one Video Creator already uses for a GAM reference
(`{from: "gam", id, in, out}` in GVC's `spec/model.py`), so a playlist can be laid onto
a timeline there without translating anything.

`asset_id` always names an asset that owns its file. A live clip added to a playlist is
stored as its parent and the clip's window (services/playlists.py), because a clip has no
bytes of its own to cut from and its window is exactly the range the item needs.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.clock import utcnow


def new_playlist_id() -> str:
    return str(uuid.uuid4())


class Playlist(SQLModel, table=True):
    id: str = Field(default_factory=new_playlist_id, primary_key=True)
    user_id: str = Field(index=True)
    name: str
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PlaylistItem(SQLModel, table=True):
    """One entry. The same asset may appear many times, at different ranges, which is
    why an item has an id of its own rather than a (playlist, asset) key."""

    __tablename__ = "playlist_item"
    # The playlist page reads its items in order; one index serves the filter and the sort.
    __table_args__ = (Index("ix_playlist_item_order", "playlist_id", "position"),)

    id: str = Field(default_factory=new_playlist_id, primary_key=True)
    playlist_id: str = Field(foreign_key="playlist.id")
    # A real foreign key, so an asset cannot be deleted from under a playlist without its
    # items going too (`services/assets._delete_dependents` removes them first).
    asset_id: str = Field(foreign_key="asset.id", index=True)
    position: int = 0
    # Seconds into the asset's own file. Both null: the whole asset. Set together.
    in_point: Optional[float] = None
    out_point: Optional[float] = None
    # How long an image stays up. Null: the default (services/playlists.IMAGE_SECONDS).
    image_seconds: Optional[float] = None
    # A name for this moment ("Bernanke on the bailout") and, from the assistant, the one
    # sentence on why it was chosen.
    label: Optional[str] = None
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
