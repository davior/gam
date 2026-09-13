import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


def new_asset_id() -> str:
    return str(uuid.uuid4())


class Asset(SQLModel, table=True):
    """One item in the library.

    Deliberately one table for everything the library holds. A clip (M7) is an Asset
    with no `storage_key` and a parent pointer; an AI generation (M8) is an Asset with
    generation metadata. Splitting them into separate tables would mean every list,
    search, filter and tag query became a union — and "show me everything about the
    Giordano interview" is the query this product exists to answer.

    Fields for those later milestones are not declared yet. Adding a column is one
    Alembic migration, which is exactly why the project has Alembic; carrying six
    milestones of unused columns is not.
    """

    # The library listing is "my assets, newest first", and it is the hottest query in
    # the application — every page load runs it. A composite index serves both the
    # filter and the sort from one structure; the separate user_id index alone would
    # leave SQLite sorting the matched rows every time.
    __table_args__ = (Index("ix_asset_user_upload", "user_id", "upload_date"),)

    id: str = Field(default_factory=new_asset_id, primary_key=True)
    user_id: str = Field(index=True)

    # ─── what the user sees and edits ────────────────────────────────────────
    # `name` is required but never blocks ingestion — it defaults to the filename
    # without its extension. FR 6.1.2: a file can be dropped in with nothing else.
    name: str
    description: Optional[str] = None
    summary: Optional[str] = None

    asset_type: str = Field(index=True)  # image | video | audio | document
    source: str = Field(default="local_upload")

    # ─── the bytes ───────────────────────────────────────────────────────────
    # Nullable because an asset need not own a file: a clip is a window into its
    # parent's bytes, not a copy of them.
    storage_key: Optional[str] = None
    thumb_key: Optional[str] = None

    original_name: Optional[str] = None
    mime_type: Optional[str] = None
    file_format: Optional[str] = None  # extension without the dot, e.g. "mp4"
    size_bytes: int = Field(default=0)
    # Computed during upload, so it costs nothing extra. Phase 2 deduplication needs
    # it; indexed now so that feature does not require a migration on a full table.
    checksum_sha256: Optional[str] = Field(default=None, index=True)

    # ─── probed from the file itself ─────────────────────────────────────────
    duration_seconds: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    codec: Optional[str] = None

    # ─── provenance of the metadata, not the file ───────────────────────────
    # JSON, {"description": "ai"|"human", ...}. FR 8.1.3 requires that a later AI run
    # never silently overwrites something a person wrote; without recording who last
    # wrote each field, that rule has nothing to check against. Written from M6, read
    # never before — but the column exists now so the first enrichment run has
    # somewhere to put it rather than needing a migration mid-milestone.
    field_provenance: str = Field(default="{}")

    # ─── time ────────────────────────────────────────────────────────────────
    # Three distinct dates, per FR 7.1.2: when it arrived, when its bytes last
    # changed, and when its metadata was last edited. Collapsing them would lose the
    # ability to answer "what have I described recently" separately from "what have I
    # added recently".
    upload_date: datetime = Field(default_factory=datetime.utcnow, index=True)
    modified_date: datetime = Field(default_factory=datetime.utcnow)
    metadata_modified_date: datetime = Field(default_factory=datetime.utcnow)
