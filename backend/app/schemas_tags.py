"""Request and response shapes for the tag API."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

# A tag long enough to be a sentence is a description filed in the wrong place.
MAX_TAG_LENGTH = 80
MAX_CATEGORY_LENGTH = 80

# Bulk tagging is for a selection, not for the whole library in one request. The cap is
# what stops a single call rewriting every row and re-indexing it inside one HTTP
# timeout; past this the client pages.
MAX_BULK_ASSETS = 500


def _not_blank(value: str, what: str) -> str:
    cleaned = " ".join((value or "").split())
    if not cleaned:
        raise ValueError(f"{what} cannot be blank")
    return cleaned


class TagRead(BaseModel):
    id: str
    name: str
    category_id: Optional[str] = None
    # Absent when the tag is read as part of an asset — counting there would be a query
    # per tag per asset, and nothing in that view shows it.
    asset_count: Optional[int] = None


class TagCreate(BaseModel):
    name: str = Field(max_length=MAX_TAG_LENGTH)
    category_id: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _clean(cls, value: str) -> str:
        return _not_blank(value, "A tag name")


class TagUpdate(BaseModel):
    """Rename, or re-file under a different category.

    `category_id` is deliberately not distinguishable from absent here: moving a tag to
    the top level is expressed by sending null, and that is the only reason a caller
    would send it at all.
    """

    name: Optional[str] = Field(default=None, max_length=MAX_TAG_LENGTH)
    category_id: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _clean(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _not_blank(value, "A tag name")


class TagCategoryRead(BaseModel):
    id: str
    name: str
    parent_category_id: Optional[str] = None


class TagCategoryCreate(BaseModel):
    name: str = Field(max_length=MAX_CATEGORY_LENGTH)
    parent_category_id: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _clean(cls, value: str) -> str:
        return _not_blank(value, "A category name")


class TagCategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=MAX_CATEGORY_LENGTH)
    parent_category_id: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _clean(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _not_blank(value, "A category name")


class AssetTagsWrite(BaseModel):
    """Attach tags to one asset, by name.

    By name rather than by id because the UI's tag box does not know whether what was
    typed exists yet, and making the client resolve that first turns one interaction
    into two round trips and a race.
    """

    names: List[str] = Field(default_factory=list, max_length=50)


class BulkTagsWrite(BaseModel):
    """Apply and remove tags across a selection in one call."""

    asset_ids: List[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)
    add: List[str] = Field(default_factory=list, max_length=50)
    remove: List[str] = Field(default_factory=list, max_length=50)


class BulkTagsResult(BaseModel):
    # What actually changed, not what was asked for: ids belonging to someone else are
    # dropped at the ownership check, and the caller should be able to see that.
    updated: int
    tags_added: List[TagRead] = Field(default_factory=list)
