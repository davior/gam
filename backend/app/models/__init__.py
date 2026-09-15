"""SQLModel tables.

Split by domain rather than kept in one file — gecko-notes' single `models.py` reached
17 tables and 400 lines before it became awkward, and GAM's asset/tag/transcript/job
tables will pass that comfortably.

Every table must be imported here: Alembic's autogenerate only sees what is registered
on `SQLModel.metadata` at import time, so a table missing from this list produces an
empty migration and a silent schema drift.
"""

from app.models.asset import Asset
from app.models.embedding import Embedding
from app.models.job import EnrichmentJob
from app.models.provider import AIProvider
from app.models.setting import UserSetting
from app.models.tag import AssetTag, Tag, TagCategory
from app.models.transcript import TranscriptSegment
from app.models.user import User

__all__ = [
    "AIProvider",
    "Asset",
    "AssetTag",
    "Embedding",
    "EnrichmentJob",
    "Tag",
    "TagCategory",
    "TranscriptSegment",
    "User",
    "UserSetting",
]
