"""Starting an AI enrichment pass over one asset.

Its own router rather than more endpoints in `transcripts.py`: that file is about a
transcript — producing one, reading it, correcting it — and `describe` and `autotag`
will land beside `summarize` here as the milestone continues. Mounted under
`/api/assets` all the same, because these are actions on an asset.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.enrichment.describe import describable
from app.enrichment.summarize import summarisable
from app.jobs import enrichment as enrichment_jobs
from app.jobs.registry import KINDS
from app.models.asset import Asset
from app.models.job import KIND_AUTOTAG, KIND_DESCRIBE, KIND_SUMMARIZE
from app.models.suggestion import Suggestion
from app.providers import build_provider
from app.schemas import DataResponse, ListResponse
from app.schemas_jobs import ActivityJobRead
from app.services import suggestions as suggestion_service

router = APIRouter()


class SuggestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    asset_id: str
    kind: str
    value: str
    status: str


def _require_provider(session: Session, user_id: str) -> None:
    """Refuse before queueing work that is certain to fail.

    The code is what the frontend branches on to offer a link to Settings — the same
    shape the embed button already uses for `embedding_unavailable`.
    """
    if build_provider(session, user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider_unavailable",
                "message": (
                    "No AI provider is configured. Add one in Settings to enable "
                    "enrichment."
                ),
            },
        )


def _owned_asset(asset_id: str, user_id: str, session: Session) -> Asset:
    asset = session.get(Asset, asset_id)
    if not asset or asset.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "No such asset"},
        )
    return asset


@router.post("/{asset_id}/summarize", response_model=DataResponse[ActivityJobRead], status_code=202)
def start_summarize(
    asset_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[ActivityJobRead]:
    """Queue a summary pass over one asset."""
    asset = _owned_asset(asset_id, user.id, session)

    # Checked here as well as in the job so the button can say what is wrong
    # immediately, rather than queueing work that is certain to fail.
    _require_provider(session, user.id)

    if not summarisable(asset):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "not_summarisable",
                "message": "This asset has no content to summarise",
            },
        )

    # One at a time per asset: two runs would bill twice for the same transcript and
    # race to write the same field.
    if enrichment_jobs.active_job(session, asset.id, KIND_SUMMARIZE) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_running",
                "message": "This asset is already being summarised",
            },
        )

    job = enrichment_jobs.submit(session, asset, KIND_SUMMARIZE)
    return DataResponse(data=KINDS["enrichment"].to_activity(job))


@router.post("/{asset_id}/autotag", response_model=DataResponse[ActivityJobRead], status_code=202)
def start_autotag(
    asset_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[ActivityJobRead]:
    """Queue a pass that proposes tags and a title. It applies neither (FR 9.1.4)."""
    asset = _owned_asset(asset_id, user.id, session)
    _require_provider(session, user.id)

    if not summarisable(asset):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "not_summarisable",
                "message": "This asset has no content to read",
            },
        )

    if enrichment_jobs.active_job(session, asset.id, KIND_AUTOTAG) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_running",
                "message": "This asset is already being tagged",
            },
        )

    job = enrichment_jobs.submit(session, asset, KIND_AUTOTAG)
    return DataResponse(data=KINDS["enrichment"].to_activity(job))


@router.post("/{asset_id}/describe", response_model=DataResponse[ActivityJobRead], status_code=202)
def start_describe(
    asset_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[ActivityJobRead]:
    """Queue a description pass over one asset — what is in it, not what it is about."""
    asset = _owned_asset(asset_id, user.id, session)
    _require_provider(session, user.id)

    if not describable(asset):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "not_summarisable",
                "message": "This asset has no content to describe",
            },
        )

    if enrichment_jobs.active_job(session, asset.id, KIND_DESCRIBE) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_running",
                "message": "This asset is already being described",
            },
        )

    job = enrichment_jobs.submit(session, asset, KIND_DESCRIBE)
    return DataResponse(data=KINDS["enrichment"].to_activity(job))


@router.get("/{asset_id}/suggestions", response_model=ListResponse[SuggestionRead])
def list_suggestions(
    asset_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> ListResponse[SuggestionRead]:
    """What is waiting for this asset. Pending only — a decision already made is not a
    thing to ask about again."""
    _owned_asset(asset_id, user.id, session)
    rows = suggestion_service.pending_for(session, asset_id)
    return ListResponse(
        data=[SuggestionRead.model_validate(r) for r in rows],
        total=len(rows),
        limit=len(rows),
        offset=0,
    )


def _owned_suggestion(
    asset_id: str, suggestion_id: str, user_id: str, session: Session
) -> Suggestion:
    row = session.get(Suggestion, suggestion_id)
    if not row or row.user_id != user_id or row.asset_id != asset_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "No such suggestion"},
        )
    return row


@router.post(
    "/{asset_id}/suggestions/{suggestion_id}/accept",
    response_model=DataResponse[SuggestionRead],
)
def accept_suggestion(
    asset_id: str,
    suggestion_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[SuggestionRead]:
    """Apply one. The only path by which an AI proposal reaches the library."""
    asset = _owned_asset(asset_id, user.id, session)
    row = _owned_suggestion(asset_id, suggestion_id, user.id, session)
    return DataResponse(
        data=SuggestionRead.model_validate(suggestion_service.accept(session, asset, row))
    )


@router.post(
    "/{asset_id}/suggestions/{suggestion_id}/reject",
    response_model=DataResponse[SuggestionRead],
)
def reject_suggestion(
    asset_id: str,
    suggestion_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[SuggestionRead]:
    """Record a no. Kept, so a later run does not propose the same thing again."""
    _owned_asset(asset_id, user.id, session)
    row = _owned_suggestion(asset_id, suggestion_id, user.id, session)
    return DataResponse(data=SuggestionRead.model_validate(suggestion_service.reject(session, row)))
