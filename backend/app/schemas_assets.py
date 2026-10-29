"""Request and response shapes for the asset API."""

from __future__ import annotations

import re
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import music

# A whole year, a year and month, or a full date. Months 01-12 and days 01-31 are
# enforced here so the string column cannot hold "2019-13" and sort between "2019-12"
# and "2020-01" — the range filters rely on lexicographic order being chronological.
_ISO_PARTIAL_DATE = re.compile(r"\d{4}(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?)?")


class AssetRead(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    summary: Optional[str] = None
    asset_type: str
    source: str

    # M7. `parent_asset_id` is provenance on any of clip/promoted/extracted; only a
    # clip (`source == "clip"`) has no file of its own and needs `in_point`/
    # `out_point` to bound playback of its parent's bytes.
    parent_asset_id: Optional[str] = None
    in_point: Optional[float] = None
    out_point: Optional[float] = None

    original_name: Optional[str] = None
    mime_type: Optional[str] = None
    file_format: Optional[str] = None
    size_bytes: int = 0

    duration_seconds: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    codec: Optional[str] = None

    # Signed and time-limited, minted per response rather than stored. A stored URL
    # would either never expire or be stale by the time it was read.
    file_url: Optional[str] = None
    thumb_url: Optional[str] = None

    # Recomputed from storage on read rather than trusted from the row, so a file that
    # vanished underneath the database shows as missing instead of as a broken image.
    missing: bool = False

    # ─── attribution (M10) ───────────────────────────────────────────────────
    # These are the *resolved* values: a clip with nothing of its own carries what it
    # inherited from its parent, so the panel shows a real credit rather than eight
    # blanks beside a video that is plainly attributed.
    source_url: Optional[str] = None
    creator: Optional[str] = None
    publisher: Optional[str] = None
    source_title: Optional[str] = None
    published_date: Optional[str] = None
    retrieved_at: Optional[datetime] = None
    license: Optional[str] = None
    credit_line: Optional[str] = None

    # The line to display: `credit_line` when one was typed, otherwise composed from the
    # fields above. Read-only and computed per response, for the same reason `file_url`
    # is: a stored copy would be stale the moment a component field changed.
    credit: str = ""
    # Which of the fields above came from the parent rather than from this row. Sent so
    # the UI can mark an inherited value as inherited instead of letting it look like
    # something typed on the clip — which is what would make a user "correct" it here
    # and quietly break the link to the source.
    attribution_inherited: List[str] = Field(default_factory=list)

    # ─── music profile ───────────────────────────────────────────────────────
    # Lists decoded from their JSON columns; empty when nothing is known. Not inherited
    # by a clip, unlike attribution: thirty seconds cut from a song can be the quiet
    # intro of a high-energy track, so the parent's profile would be a claim about the
    # wrong music.
    music_genres: List[str] = Field(default_factory=list)
    music_moods: List[str] = Field(default_factory=list)
    music_instruments: List[str] = Field(default_factory=list)
    music_bpm: Optional[float] = None
    music_key: Optional[str] = None
    # Null is "not known", which is different from False, "instrumental".
    music_vocals: Optional[bool] = None
    music_energy: Optional[str] = None
    # music | speech | sfx, null when not known. Decides transcription; see app/music.py.
    audio_kind: Optional[str] = None

    # Batch-loaded for a listing, never per row: sixty assets a page each asking for
    # their own tags is sixty queries that grow with the page.
    tags: List["AssetTagRead"] = Field(default_factory=list)

    # M8. How this asset was made, when fal.ai made it; null for everything else.
    generation: Optional["AssetGenerationRead"] = None

    # GAM-3. The GVC project and render a `gvc_export` came from; null otherwise.
    origin_project_id: Optional[str] = None
    origin_reference_id: Optional[str] = None

    upload_date: datetime
    modified_date: datetime
    metadata_modified_date: datetime


class AssetGenerationSource(BaseModel):
    """One image a generation was made from."""

    asset_id: str
    role: str  # "base" | "end_frame"
    # A snapshot from when it was generated, so a base deleted since still reads as
    # something rather than as a bare id.
    name: str = ""


class AssetGenerationRead(BaseModel):
    """The `ai_*` columns, decoded. Enough to show the provenance and to regenerate."""

    model_config = ConfigDict(protected_namespaces=())

    model: str  # the fal endpoint id
    kind: str   # text_to_image | image_to_image | image_to_video
    prompt: str
    parameters: dict = Field(default_factory=dict)
    sources: List[AssetGenerationSource] = Field(default_factory=list)
    seed: Optional[int] = None
    generated_at: Optional[datetime] = None


class AssetTagRead(BaseModel):
    """A tag as it appears on an asset.

    Deliberately thinner than the catalogue's `TagRead`: no usage count, because showing
    one here would mean counting per tag per asset, and nothing in this view displays it.
    """

    id: str
    name: str
    category_id: Optional[str] = None


class AssetUpdate(BaseModel):
    """Every field a user may edit by hand (FR 7.1.3).

    All optional: a PATCH carries only what changed. `None` and "absent" are
    distinguished by `exclude_unset`, so clearing a description is possible.
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=20_000)
    summary: Optional[str] = Field(default=None, max_length=20_000)

    # Attribution (M10). All editable by hand — the harvester only ever fills blanks,
    # and an AI may only propose, so this is the sole path that can correct a value.
    source_url: Optional[str] = Field(default=None, max_length=2_000)
    creator: Optional[str] = Field(default=None, max_length=500)
    publisher: Optional[str] = Field(default=None, max_length=500)
    source_title: Optional[str] = Field(default=None, max_length=500)
    published_date: Optional[str] = Field(default=None, max_length=10)
    retrieved_at: Optional[datetime] = None
    license: Optional[str] = Field(default=None, max_length=500)
    credit_line: Optional[str] = Field(default=None, max_length=2_000)

    # Music profile. A list replaces the stored one whole, and an empty list or null
    # clears it. The router turns lists into their JSON columns (`music.to_columns`).
    music_genres: Optional[List[str]] = Field(default=None, max_length=music.MAX_TERMS)
    music_moods: Optional[List[str]] = Field(default=None, max_length=music.MAX_TERMS)
    music_instruments: Optional[List[str]] = Field(default=None, max_length=music.MAX_TERMS)
    music_bpm: Optional[float] = Field(default=None, ge=music.MIN_BPM, le=music.MAX_BPM)
    music_key: Optional[str] = Field(default=None, max_length=music.MAX_KEY_LENGTH)
    music_vocals: Optional[bool] = None
    music_energy: Optional[Literal["low", "medium", "high"]] = None
    audio_kind: Optional[Literal["music", "speech", "sfx"]] = None

    @field_validator("music_genres", "music_moods", "music_instruments")
    @classmethod
    def _terms_are_short(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        """Refuse an over-long term rather than letting `clean_terms` drop it silently:
        a person who typed it should be told, where a tag writer's junk need not be."""
        if value is None:
            return None
        for term in value:
            if len(term.strip()) > music.MAX_TERM_LENGTH:
                raise ValueError(f"each term must be at most {music.MAX_TERM_LENGTH} characters")
        return value

    @field_validator("music_key")
    @classmethod
    def _key_blank_is_none(cls, value: Optional[str]) -> Optional[str]:
        return music.clean_key(value) if value is not None else None

    @field_validator("published_date")
    @classmethod
    def _published_date_is_an_iso_partial(cls, value: Optional[str]) -> Optional[str]:
        """`YYYY`, `YYYY-MM` or `YYYY-MM-DD`, and nothing else.

        The looseness of a string column is what lets a partial date exist at all; the
        validator is what stops it becoming a free-text field where "summer 1994" and
        "15/03/19" would sort meaninglessly and break the range filters, which compare
        lexicographically precisely because the format is guaranteed here.
        """
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if not _ISO_PARTIAL_DATE.fullmatch(cleaned):
            raise ValueError("published_date must be YYYY, YYYY-MM or YYYY-MM-DD")
        return cleaned

    @field_validator("name")
    @classmethod
    def _name_is_not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("name cannot be blank")
        return stripped


class UploadRejection(BaseModel):
    """One file in a bulk upload that could not be accepted."""

    filename: str
    code: str
    message: str


class UploadResult(BaseModel):
    """The outcome of an upload request.

    Bulk upload is partial-success by design: twenty files where one is a .exe should
    store nineteen and say why the twentieth was refused, not reject the batch.
    """

    created: list[AssetRead]
    rejected: list[UploadRejection]


class WaveformRead(BaseModel):
    """What the audio player draws its scrub bar from (`ingest/waveform.py`).

    `duration` is measured from the decoded samples, which is what the peaks are laid
    out against. The player still takes its clock from the media element; the two
    agree to within a block.
    """

    duration: float
    peaks: List[float]
