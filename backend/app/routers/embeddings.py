"""Semantic-search coverage, and filling it in.

Separate from the settings router, which is about configuration rather than the library.
`write_embedding_settings` returns `read_embedding_settings`, so folding coverage into
that response would run a whole-table query on every settings *write* — including each
provider or model change made from the panel.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.embeddings import build_embedder
from app.jobs import enrichment as enrichment_jobs
from app.jobs.registry import KINDS
from app.models.job import KIND_BACKFILL_EMBEDDINGS
from app.schemas import DataResponse
from app.schemas_jobs import ActivityJobRead
from app.search import coverage as coverage_service

router = APIRouter()


class EmbeddingCoverage(BaseModel):
    model: str
    total_assets: int
    embedded_assets: int
    pending_assets: int
    pending_segments: int


def _require_embedder(session: Session, user_id: str):
    embedder = build_embedder(session, user_id)
    if embedder is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "embedding_unavailable",
                "message": (
                    "No embedding provider is configured. Add one in Settings to enable "
                    "semantic search."
                ),
            },
        )
    return embedder


@router.get("/status", response_model=DataResponse[EmbeddingCoverage])
def read_coverage(
    user: CurrentUser, session: Session = Depends(get_session)
) -> DataResponse[EmbeddingCoverage]:
    """How much of this library semantic search can currently see.

    400s without a provider rather than returning nullable counts everywhere: with no
    provider there is no model, and every number here is relative to one. The settings
    panel only asks once it knows a provider is configured, so this is a race rather
    than a normal path.
    """
    embedder = _require_embedder(session, user.id)
    found = coverage_service.coverage(session, user.id, embedder.model)

    return DataResponse(
        data=EmbeddingCoverage(
            model=found.model,
            total_assets=found.total_assets,
            embedded_assets=found.embedded_assets,
            pending_assets=found.pending_assets,
            pending_segments=found.pending_segments,
        )
    )


@router.post("/backfill", response_model=DataResponse[ActivityJobRead], status_code=202)
def start_backfill(
    user: CurrentUser, session: Session = Depends(get_session)
) -> DataResponse[ActivityJobRead]:
    """Embed everything in this library that has no vector at the configured model.

    Deliberately does not refuse when nothing is pending: the job recomputes its own
    work list anyway — it has to, so a cancelled or restarted run resumes correctly —
    so a count taken here would be advisory at best. An empty run finishes immediately
    saying "0 embedded", which is a true answer to a reasonable question.
    """
    embedder = _require_embedder(session, user.id)

    if enrichment_jobs.active_library_job(session, user.id, KIND_BACKFILL_EMBEDDINGS):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_running",
                "message": "Your library is already being embedded",
            },
        )

    job = enrichment_jobs.submit_library(
        session, user.id, KIND_BACKFILL_EMBEDDINGS, model=embedder.model
    )
    return DataResponse(data=KINDS["enrichment"].to_activity(job))
