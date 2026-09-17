"""Starting an AI enrichment pass over one asset.

Its own router rather than more endpoints in `transcripts.py`: that file is about a
transcript — producing one, reading it, correcting it — and `describe` and `autotag`
will land beside `summarize` here as the milestone continues. Mounted under
`/api/assets` all the same, because these are actions on an asset.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, col, select

from app.auth import CurrentUser
from app.database import get_session
from app.embeddings import build_embedder
from app.enrichment import bulk
from app.enrichment.describe import describable
from app.enrichment.extract_text import extractable
from app.enrichment.summarize import summarisable
from app.jobs import enrichment as enrichment_jobs
from app.jobs.registry import KINDS
from app.models.asset import Asset
from app.models.job import (
    KIND_AUTOTAG,
    KIND_BULK_ENRICH,
    KIND_DESCRIBE,
    KIND_EMBED,
    KIND_EXTRACT_TEXT,
    KIND_GENERATE_ALL,
    KIND_SUMMARIZE,
)
from app.models.document import DocumentPage
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


@router.post(
    "/{asset_id}/generate-all", response_model=DataResponse[ActivityJobRead], status_code=202
)
def start_generate_all(
    asset_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[ActivityJobRead]:
    """Queue summarize, describe and autotag together — the one-button version of
    pressing each in turn."""
    asset = _owned_asset(asset_id, user.id, session)
    _require_provider(session, user.id)

    if not summarisable(asset):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "not_summarisable",
                "message": "This asset has no content to generate from",
            },
        )

    if enrichment_jobs.active_job(session, asset.id, KIND_GENERATE_ALL) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_running",
                "message": "This asset is already generating metadata",
            },
        )

    job = enrichment_jobs.submit(session, asset, KIND_GENERATE_ALL)
    return DataResponse(data=KINDS["enrichment"].to_activity(job))


class DocumentPageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    idx: int
    page_number: Optional[int] = None
    label: Optional[str] = None
    text: str


@router.post(
    "/{asset_id}/extract-text", response_model=DataResponse[ActivityJobRead], status_code=202
)
def start_extract_text(
    asset_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[ActivityJobRead]:
    """Queue a text extraction over one document.

    No `_require_provider`: this is the one enrichment action that calls nothing and
    costs nothing. Requiring a provider here would make configuring an LLM a
    precondition for reading a PDF, which it is not — and the whole point of this job is
    to be the step that runs *before* one is any use.
    """
    asset = _owned_asset(asset_id, user.id, session)

    if not extractable(asset):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "not_extractable",
                "message": (
                    "Text can only be read out of PDF, Word, PowerPoint, Excel and "
                    "plain-text files."
                ),
            },
        )

    if enrichment_jobs.active_job(session, asset.id, KIND_EXTRACT_TEXT) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_running",
                "message": "This document's text is already being read",
            },
        )

    job = enrichment_jobs.submit(session, asset, KIND_EXTRACT_TEXT)
    return DataResponse(data=KINDS["enrichment"].to_activity(job))


@router.get("/{asset_id}/text", response_model=ListResponse[DocumentPageRead])
def list_document_text(
    asset_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> ListResponse[DocumentPageRead]:
    """What extraction read out of this document, in order.

    Exists so extracted text is visible rather than merely stored. Text the user cannot
    see is indistinguishable from text that was never extracted, and this repository has
    enough of that already.
    """
    _owned_asset(asset_id, user.id, session)
    rows = session.exec(
        select(DocumentPage)
        .where(DocumentPage.asset_id == asset_id)
        .order_by(col(DocumentPage.idx))
    ).all()
    return ListResponse(
        data=[DocumentPageRead.model_validate(r) for r in rows],
        total=len(rows),
        limit=len(rows),
        offset=0,
    )


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


class BulkEnrichRequest(BaseModel):
    action: str
    asset_ids: list[str]


@router.post("/bulk/enrich", response_model=DataResponse[ActivityJobRead], status_code=202)
def start_bulk_enrich(
    payload: BulkEnrichRequest,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[ActivityJobRead]:
    """Run one enrichment action over a chosen set of assets.

    The ids come from the browser, so ownership is checked per asset inside the job
    rather than trusted here — a selection is not a capability.
    """
    if payload.action not in bulk.ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "bad_request",
                "message": f"Cannot run '{payload.action}' over a selection",
            },
        )

    if not payload.asset_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "bad_request", "message": "Nothing selected"},
        )

    if len(payload.asset_ids) > bulk.MAX_SELECTION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "selection_too_large",
                "message": (
                    f"Select at most {bulk.MAX_SELECTION} assets at a time. "
                    "A larger run is hard to review before it spends money."
                ),
            },
        )

    # Embedding is the one action with a different provider behind it, so it is the one
    # that must not be gated on an LLM being configured.
    if payload.action == KIND_EMBED:
        if build_embedder(session, user.id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "embedding_unavailable",
                    "message": (
                        "No embedding provider is configured. Add one in Settings to "
                        "enable semantic search."
                    ),
                },
            )
    else:
        _require_provider(session, user.id)

    # One bulk run at a time. Two would interleave in the activity feed and race each
    # other for the same rate limit.
    if enrichment_jobs.active_library_job(session, user.id, KIND_BULK_ENRICH) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_running",
                "message": "A bulk run is already going",
            },
        )

    job = enrichment_jobs.submit_library(
        session,
        user.id,
        KIND_BULK_ENRICH,
        payload=bulk.encode(payload.action, payload.asset_ids),
    )
    return DataResponse(data=KINDS["enrichment"].to_activity(job))
