"""The shape a background job is reported in."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ActivityJobRead(BaseModel):
    """One job, in the generalised form the activity API returns.

    Deliberately flat and kind-agnostic: the UI shows a single list of "things that are
    happening", and a client that had to branch on job kind to read a progress bar
    would need changing every time a kind is added.
    """

    id: str
    kind: str      # which table — "enrichment"
    action: str    # what it does — "transcribe", "describe", …
    status: str
    # True when the row is still marked active but has stopped reporting. The sweeper
    # will fail it shortly; until then the UI should not claim it is running.
    stalled: bool = False

    stage: str = ""
    progress: int = 0
    detail: str = ""

    asset_id: Optional[str] = None
    asset_name: str = ""
    model: str = ""

    # M7 only, and only once a `KIND_EXTRACT_SUBVIDEO` "extract" job has finished: the
    # new asset it created. `asset_id` stays pointed at the *source* throughout that
    # job's life, so this is the one place the frontend can learn the result's id —
    # there is nowhere else to put it in a deliberately flat, kind-agnostic shape.
    result_asset_id: Optional[str] = None

    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
