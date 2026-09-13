import uuid
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


class TranscriptSegment(SQLModel, table=True):
    """One timestamped stretch of speech.

    Rows rather than a JSON blob hung off the asset, which is what the requirements
    describe. FR 10.1.4 asks search to return *a matching snippet with its timestamp*,
    so each segment has to be individually indexable (M5's FTS5) and individually
    embeddable (M5's vectors). A blob can be neither. The API still serialises the
    nested shape the spec describes.
    """

    # Reading a transcript is always "every segment of this asset, in order", and it is
    # the only way these are ever read.
    __table_args__ = (Index("ix_segment_asset_idx", "asset_id", "idx"),)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    asset_id: str = Field(index=True)
    user_id: str = Field(index=True)

    # Position in the transcript. Kept explicit rather than inferred from start_time,
    # so hand-editing a timestamp cannot reorder the transcript underneath the reader.
    idx: int = Field(default=0)

    text: str = Field(default="")
    start_time: float = Field(default=0.0)
    end_time: float = Field(default=0.0)

    # Who was speaking, when diarisation identified it. Null when it did not.
    speaker: Optional[int] = None

    # Word-level timings as JSON: [{"w": "deploying", "s": 412.0, "e": 413.1}, ...].
    # Kept as text rather than its own table because a segment's words are only ever
    # read with that segment — a row each would multiply the table by twenty for no
    # query that needs it. Short keys because this is the bulkiest column in the
    # database and a long interview has tens of thousands of words.
    words: str = Field(default="[]")

    # True once a person has corrected this segment, so a re-run of transcription can
    # leave their edit alone rather than overwriting it (the FR 8.1.3 principle,
    # applied per segment).
    edited: bool = Field(default=False)
