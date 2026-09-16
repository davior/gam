import uuid
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


class DocumentPage(SQLModel, table=True):
    """One readable chunk of a document, in the unit that document naturally has.

    Rows rather than a column on `Asset`, for a reason that only shows up at scale:
    SQLModel loads every column of a row it selects, and the library listing selects
    assets by the page. A 200-page PDF's text in an `Asset` column would be dragged
    through every one of those listings to render a thumbnail grid that never looks at
    it.

    Shaped after `TranscriptSegment` minus the timings, and for the same reason it gives:
    a chunk is the unit that will be individually indexed and individually embedded when
    full-document search lands. Storing one blob per asset would foreclose that.
    """

    # Reading extracted text is always "every chunk of this asset, in order", which is
    # the same access pattern transcripts have and the same index shape.
    __table_args__ = (Index("ix_documentpage_asset_idx", "asset_id", "idx"),)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    asset_id: str = Field(index=True)
    user_id: str = Field(index=True)

    # Position in the document. Always present, and the only thing ordering depends on —
    # `page_number` cannot serve, because half the formats here do not have one.
    idx: int = Field(default=0)

    # The real page number, where the format has real pages: PDF pages and PPTX slides
    # do, and are 1-based to match what the reader sees. A DOCX has no pagination at all
    # until something renders it, and a plain text file has none ever, so this is null
    # for them rather than a chunk index wearing a page number's name.
    page_number: Optional[int] = None

    # What to call this chunk in the UI — "Page 3", "Slide 7", "Sheet 'Q3 Revenue'",
    # "Part 2". Stored rather than derived because a spreadsheet's unit is named by the
    # author and cannot be reconstructed from an index.
    label: Optional[str] = None

    text: str = Field(default="")
