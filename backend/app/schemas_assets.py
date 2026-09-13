"""Request and response shapes for the asset API."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class AssetRead(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    summary: Optional[str] = None
    asset_type: str
    source: str

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

    upload_date: datetime
    modified_date: datetime
    metadata_modified_date: datetime


class AssetUpdate(BaseModel):
    """Every field a user may edit by hand (FR 7.1.3).

    All optional: a PATCH carries only what changed. `None` and "absent" are
    distinguished by `exclude_unset`, so clearing a description is possible.
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=20_000)
    summary: Optional[str] = Field(default=None, max_length=20_000)

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
