"""Pictures of a PDF's pages."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.models.asset import Asset
from app.routers.assets import get_storage
from app.schemas import DataResponse
from app.schemas_assets import AssetRead
from app.services import assets as asset_service
from app.services import pdf_grab
from app.services import tags as tag_service
from app.storage import LocalStorage

router = APIRouter()


def _owned_asset(asset_id: str, user_id: str, session: Session) -> Asset:
    asset = session.get(Asset, asset_id)
    if not asset or asset.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "No such asset"},
        )
    return asset


class PageGrab(BaseModel):
    page: int = Field(ge=1)
    scale: float = Field(default=pdf_grab.DEFAULT_SCALE, ge=pdf_grab.MIN_SCALE, le=pdf_grab.MAX_SCALE)
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)


class PageCount(BaseModel):
    page_count: int


@router.get("/{asset_id}/pages", response_model=DataResponse[PageCount])
def pages(
    asset_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> DataResponse[PageCount]:
    """How many pages a PDF has, so a caller can offer a page picker."""
    asset = _owned_asset(asset_id, user.id, session)
    if not pdf_grab.is_pdf(asset):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "not_a_pdf", "message": "Only a PDF has pages to grab."},
        )
    return DataResponse(data=PageCount(page_count=pdf_grab.page_count(storage, asset)))


@router.post("/{asset_id}/grab-page", response_model=DataResponse[AssetRead], status_code=201)
def grab_page(
    asset_id: str,
    payload: PageGrab,
    user: CurrentUser,
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> DataResponse[AssetRead]:
    """Render one page to a picture and file it in the library as an image."""
    pdf = _owned_asset(asset_id, user.id, session)
    try:
        grabbed = pdf_grab.grab_page(
            session, storage, pdf, payload.page, scale=payload.scale, name=payload.name
        )
    except pdf_grab.GrabError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "cannot_grab_page", "message": str(exc)},
        ) from exc
    return DataResponse(
        data=asset_service.to_read_model(
            grabbed, storage, tag_service.tags_for(session, grabbed.id), parent=pdf
        )
    )
