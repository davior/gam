"""What each call to a paid service cost, so the library can answer "how much".

Ported from gecko-notes (`backend/app/models.py:188`) with one addition and one
deliberate omission.

The addition is `asset_id`: FR 8.1.4 wants cost per asset as well as per library, and
gecko-notes has nothing to attribute spend to. It is a plain indexed column and *not* a
foreign key, which is the omission — deleting an asset must not delete the record of
what was spent on it. A spend history that quietly shrinks when you tidy your library is
not a spend history.

The field that matters most is `cost_estimated`. A figure derived from `usage/pricing.py`
is a published list price, not a bill: providers change prices and offer cache and volume
discounts the table does not model. Nothing in this app may show a cost without saying
which kind it is.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.clock import utcnow

# What was consumed. Kept as GAM's own vocabulary rather than gecko-notes' — it has no
# `tts`.
KIND_AI = "ai"
KIND_STT = "stt"
# M8: what fal.ai made.
KIND_IMAGE = "image"
KIND_VIDEO = "video"

UNIT_TOKENS = "tokens"
UNIT_SECONDS = "seconds"
# fal bills each endpoint in its own unit. `generated_seconds` is deliberately not
# `seconds`: the usage totals already sum that as audio transcribed, and four seconds of
# generated video counted as four seconds of Deepgram would corrupt both figures.
UNIT_IMAGES = "images"
UNIT_MEGAPIXELS = "megapixels"
UNIT_GENERATED_SECONDS = "generated_seconds"
UNIT_VIDEOS = "videos"


def new_usage_id() -> str:
    return str(uuid.uuid4())


class UsageEvent(SQLModel, table=True):
    __table_args__ = (Index("ix_usageevent_user_created", "user_id", "created_at"),)

    id: str = Field(default_factory=new_usage_id, primary_key=True)
    user_id: str = Field(index=True)
    # Nullable: a library-wide job is not attributable to one asset. No foreign key, on
    # purpose — see the module docstring.
    asset_id: Optional[str] = Field(default=None, index=True)

    kind: str = Field(index=True)
    # The provider's own type, not the protocol it spoke. A DeepSeek provider addressed
    # over the Anthropic endpoint is DeepSeek spend, and costing it as Anthropic would be
    # wrong by an order of magnitude.
    provider: Optional[str] = Field(default=None, index=True)
    model: str = Field(default="")

    units: int = Field(default=0)
    unit_type: str = Field(default="")

    # None when no estimate is possible — a `custom` endpoint, or a model family the
    # table does not know. Storing a zero there would read as "this was free".
    cost: Optional[float] = None
    currency: Optional[str] = None
    # True for a list-price estimate. False is a provider-billed exact amount, which only
    # fal.ai produces: its `x-fal-billable-units` header times its own pricing API's
    # unit price (M8).
    cost_estimated: Optional[bool] = None
    # The provider's id for the call, so a figure here can be found in the provider's
    # own billing — fal's `x-fal-request-id`. Not indexed: nothing looks it up from here.
    external_ref: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow, index=True)
