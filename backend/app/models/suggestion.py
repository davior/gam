"""What the AI proposed, waiting for a person to say yes.

FR 9.1.4: an autotag run never applies its own tags. The obvious implementation — a
`status` column on `AssetTag` — was rejected, and the reason is worth recording: every
existing tag query (`tags_for`, `usage_counts`, the tag filter, and above all
`tags_text_for`, which feeds the keyword index) would then have to remember to exclude
suggested rows, and the first one that forgot would put an unapproved tag into search.
That is "applied silently" with extra steps.

So a suggestion lives here and nowhere else. It becomes a real `Tag` and `AssetTag` only
when accepted, which means nothing in the rest of the app needs to know this table
exists in order to keep behaving correctly.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.clock import utcnow

# What is being proposed. `title` is here rather than in a field of its own because
# accepting one is the same act as accepting a tag — see docs/m6-ai-enrichment.md on why
# a generated title is a suggestion and not a direct write: `name` is never empty, so
# writing it replaces something rather than filling a blank.
KIND_TAG = "tag"
KIND_TITLE = "title"
# M10. One row per proposed attribution field, with `value` holding JSON-as-TEXT:
# {"field": "publisher", "value": "BBC Two", "evidence": "the chyron reads BBC TWO"}.
#
# One kind rather than eight, because `accept` dispatches on `kind` and eight near
# identical branches would be eight places to forget one. Still one row per field, so a
# user can take the publisher and decline the date — which is the common case, since a
# model reads a channel logo far more reliably than a broadcast date.
#
# `evidence` is not decoration: attribution is the one enrichment that may not write
# directly, because a fabricated citation is worse than a blank one, and a reviewer who
# cannot see what the model read is not really reviewing anything.
KIND_ATTRIBUTION = "attribution"
KINDS = (KIND_TAG, KIND_TITLE, KIND_ATTRIBUTION)

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"


def new_suggestion_id() -> str:
    return str(uuid.uuid4())


class Suggestion(SQLModel, table=True):
    __table_args__ = (Index("ix_suggestion_asset_status", "asset_id", "status"),)

    id: str = Field(default_factory=new_suggestion_id, primary_key=True)
    user_id: str = Field(index=True)
    asset_id: str = Field(index=True, foreign_key="asset.id")

    kind: str
    value: str

    status: str = Field(default=STATUS_PENDING, index=True)
    # Which model proposed it. Kept because a suggestion outlives the provider that
    # made it, and "who said this" is the first question when one is obviously wrong.
    model: str = Field(default="")

    created_at: datetime = Field(default_factory=utcnow)
    # A rejected suggestion is kept rather than deleted, so a re-run can avoid proposing
    # the same thing again and the user is not asked twice about a tag they declined.
    resolved_at: Optional[datetime] = None
