import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.clock import utcnow


# Who last wrote a field, as recorded in `Asset.field_provenance`.
#
# "embedded" is a third value beside the original two, and the distinction it draws is
# load-bearing: a tag read out of a file (EXIF Artist, an ID3 frame, a PDF Author) is a
# fact about the file but not a claim anybody checked — it is frequently the camera
# owner, a studio default, or boilerplate. Keeping it separate from "human" is what lets
# a later pass propose over a camera-supplied name while never proposing over something
# the user typed. Collapse the two and that distinction is gone for good, because
# nothing else records it.
PROVENANCE_HUMAN = "human"
PROVENANCE_AI = "ai"
PROVENANCE_EMBEDDED = "embedded"


def new_asset_id() -> str:
    return str(uuid.uuid4())


class Asset(SQLModel, table=True):
    """One item in the library.

    Deliberately one table for everything the library holds. A clip (M7) is an Asset
    with no `storage_key` and a parent pointer; an AI generation (M8) is an Asset with
    generation metadata. Splitting them into separate tables would mean every list,
    search, filter and tag query became a union — and "show me everything about the
    Giordano interview" is the query this product exists to answer.

    M8's fields are not declared yet — that is still one Alembic migration away, and
    carrying unused columns for a milestone not yet started is not worth it.
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

    # ─── clips (M7) ──────────────────────────────────────────────────────────
    # A clip is *identified* by `storage_key IS NULL AND parent_asset_id IS NOT
    # NULL` — this column is the physical fact a delete guard depends on, not a label.
    # A real foreign key (not the transcriptsegment/documentpage pattern of no FK plus
    # explicit cleanup): unlike those, a live clip is meant to *block* its parent's
    # deletion until it is promoted or removed, and SQLite enforcing that is the
    # safety net behind `services/assets.py::delete_asset`'s own guard, the same
    # belt-and-suspenders role the FK already plays for `assettag`/`suggestion`.
    parent_asset_id: Optional[str] = Field(default=None, foreign_key="asset.id", index=True)
    # Seconds into the parent. Both set together, always by the code that creates the
    # clip or extraction — never edited afterwards (M7 does not build a trim editor).
    in_point: Optional[float] = None
    out_point: Optional[float] = None

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

    # ─── transcript header ───────────────────────────────────────────────────
    # The segments live in their own table; this is what was run and how it went, so
    # the library can show "transcribed" without counting rows, and so a transcript
    # records which model produced it rather than whatever is configured today.
    transcript_status: Optional[str] = None  # none|running|done|error
    transcript_model: Optional[str] = None
    transcript_language: Optional[str] = None

    # ─── attribution (M10) ───────────────────────────────────────────────────
    # Whose work this is, as opposed to `source` above, which is how the file got here.
    # The two get conflated constantly; they answer different questions and neither
    # substitutes for the other. Specified in docs/m10-attribution.md.
    source_url: Optional[str] = None
    creator: Optional[str] = None       # author, photographer, speaker, director
    publisher: Optional[str] = None     # outlet, channel, studio, imprint
    source_title: Optional[str] = None  # the programme, film, article or book
    # A string, not a datetime, against this file's own convention two blocks down —
    # and deliberately. Publication dates are routinely partial: a book is from 1994, a
    # magazine piece from March 2019. A datetime cannot hold either without inventing a
    # January 1st that then reads as a real one, which in a citation record is exactly
    # the quiet falsehood this milestone exists to prevent. Stores ISO 8601 `YYYY`,
    # `YYYY-MM` or `YYYY-MM-DD`, validated on write in schemas_assets.AssetUpdate.
    # ISO partial dates compare correctly as plain strings ("2018-12-31" < "2019" <
    # "2019-03-01"), so the date filters need no parsing and no special cases.
    published_date: Optional[str] = Field(default=None, index=True)
    # A real datetime, because a download happened at an instant — there is no
    # partial-precision case here to serve.
    retrieved_at: Optional[datetime] = None
    license: Optional[str] = None
    # The displayed citation, and an *override* only — null until somebody types one.
    # The value shown is composed from the fields above on read (app/attribution.py).
    # Storing the composition instead would leave it stale the moment `publisher` is
    # corrected, which is the same rot that made copy-on-create the wrong answer for a
    # clip's inherited attribution one level down.
    credit_line: Optional[str] = None

    # ─── provenance of the metadata, not the file ───────────────────────────
    # JSON, {"description": "ai"|"human"|"embedded", ...}. FR 8.1.3 requires that a later AI run
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
    upload_date: datetime = Field(default_factory=utcnow, index=True)
    modified_date: datetime = Field(default_factory=utcnow)
    metadata_modified_date: datetime = Field(default_factory=utcnow)
