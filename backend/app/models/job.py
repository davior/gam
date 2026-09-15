import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.clock import utcnow

# The actions a job can perform. One table rather than one per action: they share a
# lifecycle, a progress shape and a cancel button, and the activity API would otherwise
# become a union over near-identical tables.
#
# describe/summarize/autotag are declared ahead of M6 because they are in the per-asset
# set below and cost nothing to name.
#
# `backfill_embeddings` was removed in #11 as dead code, with a note saying library-wide
# embedding was bulk enrichment and belonged in M6. Removing an unreferenced constant was
# right; that stated reason was wrong on the facts, and is corrected here rather than
# quietly reversed. docs/plan-of-attack.md lists M5's contents as "embedding provider +
# embed job + backfill", and embeddings/ollama.py justifies its choice of endpoint as
# "the difference between one request and ten thousand on a backfill". Both embedders
# were written expecting this. It returns with a caller.
KIND_TRANSCRIBE = "transcribe"
KIND_DESCRIBE = "describe"
KIND_SUMMARIZE = "summarize"
KIND_AUTOTAG = "autotag"
KIND_EMBED = "embed"
KIND_BACKFILL_EMBEDDINGS = "backfill_embeddings"
# One action applied across a chosen set of assets. Which action, and which assets,
# live in `EnrichmentJob.payload` — see there for why it is one job and not N.
KIND_BULK_ENRICH = "bulk_enrich"

# Per-asset actions. Everything in here requires an `asset_id`.
ENRICHMENT_KINDS = frozenset(
    {
        KIND_TRANSCRIBE,
        KIND_DESCRIBE,
        KIND_SUMMARIZE,
        KIND_AUTOTAG,
        KIND_EMBED,
    }
)

# Actions with no single asset, which is the reason `asset_id` is nullable — the worker
# branches on this set rather than on a hardcoded kind, so adding another one needs no
# change to the dispatch. A bulk run over a selection is here too: it has many assets,
# which for the purposes of `asset_id` is the same as having none.
LIBRARY_KINDS = frozenset({KIND_BACKFILL_EMBEDDINGS, KIND_BULK_ENRICH})


class EnrichmentJob(SQLModel, table=True):
    """One "do this to that asset" task, run on a worker thread.

    Follows gecko-notes' job-table shape — a queued row a background worker picks up,
    with the progress fields multi-minute work needs. `updated_at` doubles as the
    heartbeat: a row that stops advancing is a worker that stopped working.
    """

    # The activity indicator polls "my active jobs", so it filters on both columns.
    __table_args__ = (Index("ix_enrichmentjob_user_status", "user_id", "status"),)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(index=True)
    # Null for a whole-library job (see LIBRARY_KINDS). `ActivityJobRead.asset_id` was
    # already Optional and the frontend type already `string | null`, so the read path
    # anticipated this shape before the column allowed it.
    asset_id: Optional[str] = Field(default=None, index=True)

    kind: str = Field(index=True)
    status: str = Field(default="queued", index=True)  # queued|processing|done|error|cancelled

    stage: str = Field(default="")     # "Extracting audio" | "Transcribing"
    progress: int = Field(default=0)   # 0-100
    detail: str = Field(default="")

    # Snapshot, so the activity row still reads sensibly after the asset is renamed or
    # deleted. The id is the source of truth; this is for display only.
    asset_name: str = Field(default="")

    # The model actually used, resolved at enqueue time rather than read back from
    # settings later — otherwise a transcript records whatever is configured now, not
    # what produced it.
    model: str = Field(default="")

    error_message: Optional[str] = None

    # What a job needs that does not fit a column, JSON-as-TEXT. Only bulk runs use it
    # today: {"action": "summarize", "asset_ids": [...]}.
    #
    # The selection is stored on the job rather than turned into one job per asset, for
    # the reason `enrichment/backfill.py` already gives: cancellation is per row, so
    # forty per-asset jobs mean forty Cancel clicks and forty near-identical lines in the
    # activity feed. It also puts the consecutive-failure cutoff in one place — a
    # rejected API key fails identically on every asset, and N separate jobs have nowhere
    # to notice that.
    payload: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)
