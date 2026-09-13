"""Reading and cancelling background work.

Polled, not streamed. gecko-notes made the same call and its reasoning holds: a client
that polls can rebuild its state after a reload, which a dropped event stream cannot.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.jobs import registry
from app.schemas import DataResponse, ListResponse
from app.schemas_jobs import ActivityJobRead

router = APIRouter()


@router.get("", response_model=ListResponse[ActivityJobRead])
def list_activity(
    user: CurrentUser,
    active: bool = Query(default=False, description="Only jobs still queued or running"),
    asset_id: Optional[str] = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    session: Session = Depends(get_session),
) -> ListResponse[ActivityJobRead]:
    jobs = registry.list_jobs(
        session, user.id, active_only=active, asset_id=asset_id, limit=limit
    )
    return ListResponse[ActivityJobRead](data=jobs, total=len(jobs), limit=limit, offset=0)


@router.get("/{kind}/{job_id}", response_model=DataResponse[ActivityJobRead])
def get_activity(
    kind: str,
    job_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[ActivityJobRead]:
    job = registry.get_job(session, user.id, kind, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "No such job"},
        )
    return DataResponse(data=job)


@router.delete("/{kind}/{job_id}", response_model=DataResponse[ActivityJobRead])
def cancel_activity(
    kind: str,
    job_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[ActivityJobRead]:
    """Ask a job to stop.

    Returns the job rather than 204, so the client can render the new state without a
    follow-up request. Cancelling a job that has already finished is not an error — the
    user pressed a button that was true when they saw it.
    """
    job = registry.cancel_job(session, user.id, kind, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "No such job"},
        )
    return DataResponse(data=job)
