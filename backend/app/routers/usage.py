"""What the library has spent, and on what.

FR 8.1.4. Every response here carries `estimated`, and the frontend is required to say
so: a figure from `usage/pricing.py` is a published list price, not a bill, and a number
shown without that qualification is a claim this app cannot support.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.models.asset import Asset
from app.schemas import DataResponse
from app.usage import events as usage_events

router = APIRouter()


class ProviderTotal(BaseModel):
    provider: str
    events: int
    units: int
    # None when nothing in this group could be priced — a `custom` endpoint, or a model
    # family the table does not know.
    cost: float | None = None


class UsageTotals(BaseModel):
    total_events: int
    # Fewer than `total_events` means some calls could not be priced, and the total below
    # is therefore a floor rather than the whole story. The UI says so.
    priced_events: int
    cost: float
    currency: str
    estimated: bool
    tokens: int
    seconds: int


class UsageSummary(BaseModel):
    totals: UsageTotals
    by_provider: list[ProviderTotal]


@router.get("", response_model=DataResponse[UsageSummary])
def read_usage(
    user: CurrentUser, session: Session = Depends(get_session)
) -> DataResponse[UsageSummary]:
    return DataResponse(
        data=UsageSummary(
            totals=UsageTotals(**usage_events.totals_for(session, user.id)),
            by_provider=[
                ProviderTotal(**row) for row in usage_events.by_provider(session, user.id)
            ],
        )
    )


@router.get("/assets/{asset_id}", response_model=DataResponse[UsageTotals])
def read_asset_usage(
    asset_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[UsageTotals]:
    """What has been spent on one asset.

    The asset is checked rather than trusted, even though a usage row carries its own
    user_id: an id that is not yours should not be a way to learn that it exists.
    """
    asset = session.get(Asset, asset_id)
    if not asset or asset.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "No such asset"},
        )

    return DataResponse(data=UsageTotals(**usage_events.totals_for(session, user.id, asset_id)))
