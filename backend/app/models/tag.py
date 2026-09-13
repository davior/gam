"""Tags, the categories that organise them, and what they are attached to.

Three tables rather than a JSON column on `Asset`, because every question worth asking
of a tag runs the wrong way for a blob: "which assets are tagged X", "what tags exist",
"rename X everywhere", "how many things use X". A denormalised list answers none of them
without scanning the whole library.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel

from app.clock import utcnow


def new_tag_id() -> str:
    return str(uuid.uuid4())


class TagCategory(SQLModel, table=True):
    """An optional grouping for tags, nestable to any depth.

    `parent_category_id` pointing at this same table is what makes "People > Scientists"
    possible without a fixed two-level schema. It is also the reason
    `services.tags.would_create_cycle` exists: nothing in the column type stops a
    category becoming its own ancestor, and the recursive CTE that resolves a subtree
    does not terminate if one does.
    """

    id: str = Field(default_factory=new_tag_id, primary_key=True)
    user_id: str = Field(index=True)
    name: str
    parent_category_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class Tag(SQLModel, table=True):
    """One label, owned by one user.

    `name` keeps the case it was typed in — "NATO" and "Klaus Schwab" are how they should
    read back, and a lowercased column loses that permanently. Uniqueness is enforced
    case-insensitively instead, by the expression index below, so typing "nato" attaches
    the existing "NATO" rather than creating a second tag that looks identical in the UI
    and splits the library in two.
    """

    __table_args__ = (
        Index("ux_tag_user_lower_name", "user_id", text("lower(name)"), unique=True),
    )

    id: str = Field(default_factory=new_tag_id, primary_key=True)
    user_id: str = Field(index=True)
    name: str
    category_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class AssetTag(SQLModel, table=True):
    """The join. Composite primary key, so attaching twice is impossible by construction
    rather than by a caller remembering to check.

    The PK orders asset first, which serves "what is this asset tagged with". The
    separate index on `tag_id` serves the other direction — "which assets have this tag"
    — which is the filter the library runs, and which the PK alone cannot answer without
    a scan.
    """

    __table_args__ = (Index("ix_assettag_tag", "tag_id"),)

    asset_id: str = Field(primary_key=True, foreign_key="asset.id")
    tag_id: str = Field(primary_key=True, foreign_key="tag.id")
    created_at: datetime = Field(default_factory=utcnow)
