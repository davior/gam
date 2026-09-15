"""Starting an AI enrichment pass over one asset.

Its own router rather than more endpoints in `transcripts.py`: that file is about a
transcript — producing one, reading it, correcting it — and `describe` and `autotag`
will land beside `summarize` here as the milestone continues. Mounted under
`/api/assets` all the same, because these are actions on an asset.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.enrichment.summarize import summarisable
from app.jobs import enrichment as enrichment_jobs
from app.jobs.registry import KINDS
from app.models.asset import Asset
from app.models.job import KIND_SUMMARIZE
from app.providers import build_provider
from app.schemas import DataResponse
from app.schemas_jobs import ActivityJobRead

router = APIRouter()


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
    # immediately, rather than queueing work that is certain to fail. The code is what
    # the frontend branches on to offer a link to Settings — the same shape the embed
    # button already uses for `embedding_unavailable`.
    if build_provider(session, user.id) is None:
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
