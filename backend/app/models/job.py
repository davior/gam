import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

# The enrichment actions a job can perform. One table rather than one per action:
# they share a lifecycle, a progress shape and a cancel button, and the activity API
# would otherwise become a union over five near-identical tables.
KIND_TRANSCRIBE = "transcribe"
KIND_DESCRIBE = "describe"
KIND_SUMMARIZE = "summarize"
KIND_AUTOTAG = "autotag"
KIND_EMBED = "embed"
KIND_BACKFILL_EMBEDDINGS = "backfill_embeddings"

ENRICHMENT_KINDS = frozenset(
    {
        KIND_TRANSCRIBE,
        KIND_DESCRIBE,
        KIND_SUMMARIZE,
        KIND_AUTOTAG,
        KIND_EMBED,
    }
)


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
    asset_id: str = Field(index=True)

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

    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
