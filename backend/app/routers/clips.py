"""Clips (non-destructive) and sub-video extraction (destructive), over an asset."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.ingest.filetypes import TYPE_AUDIO, TYPE_VIDEO
from app.jobs import enrichment as enrichment_jobs
from app.jobs.registry import KINDS
from app.models.asset import Asset
from app.models.job import KIND_EXTRACT_SUBVIDEO
from app.routers.assets import get_storage
from app.schemas import DataResponse, ListResponse
from app.schemas_assets import AssetRead
from app.schemas_jobs import ActivityJobRead
from app.services import assets as asset_service
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


def _require_no_active_extraction(session: Session, asset_id: str) -> None:
    # One at a time per asset, same reason `start_transcription` checks this: two
    # concurrent runs would race to write the same clip row, or produce two standalone
    # assets for the same request.
    if enrichment_jobs.active_job(session, asset_id, KIND_EXTRACT_SUBVIDEO) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_running",
                "message": "A sub-video extraction is already running for this asset",
            },
        )


class ClipCreate(BaseModel):
    in_point: float = Field(ge=0)
    out_point: float = Field(gt=0)
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)


class SubvideoCreate(BaseModel):
    in_point: float = Field(ge=0)
    out_point: float = Field(gt=0)
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)


# ─── non-destructive clips ─────────────────────────────────────────────────────


@router.post(
    "/{asset_id}/clips", response_model=DataResponse[AssetRead], status_code=status.HTTP_201_CREATED
)
def create_clip(
    asset_id: str,
    payload: ClipCreate,
    user: CurrentUser,
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> DataResponse[AssetRead]:
    """A window into the parent's bytes: no file, no ffmpeg, no job — a 201, not a 202."""
    parent = _owned_asset(asset_id, user.id, session)
    try:
        clip = asset_service.create_clip(
            session,
            parent,
            in_point=payload.in_point,
            out_point=payload.out_point,
            name=payload.name,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_clip_range", "message": str(exc)},
        ) from exc
    return DataResponse(data=asset_service.to_read_model(clip, storage, parent=parent))


@router.get("/{asset_id}/clips", response_model=ListResponse[AssetRead])
def list_clips(
    asset_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> ListResponse[AssetRead]:
    """Everything derived from this asset: live clips and past promotions/extractions.

    Unfiltered — a "Clips" tab wants the whole history, and the delete guard's promote
    UI narrows this to `source == "clip"` itself rather than needing a second endpoint.
    """
    parent = _owned_asset(asset_id, user.id, session)
    rows = asset_service.list_children(session, parent.id, user.id)
    tags_by_asset = tag_service.tags_for_many(session, [row.id for row in rows])
    return ListResponse[AssetRead](
        data=[
            asset_service.to_read_model(row, storage, tags_by_asset.get(row.id, []), parent=parent)
            for row in rows
        ],
        total=len(rows),
        limit=len(rows),
        offset=0,
    )


# ─── sub-video extraction ───────────────────────────────────────────────────────


@router.post("/{asset_id}/subvideo", response_model=DataResponse[ActivityJobRead], status_code=202)
def start_subvideo_extraction(
    asset_id: str,
    payload: SubvideoCreate,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[ActivityJobRead]:
    """Queue a fresh, standalone extraction. Nothing about the source asset changes."""
    asset = _owned_asset(asset_id, user.id, session)
    if asset.asset_type not in (TYPE_VIDEO, TYPE_AUDIO) or not asset.storage_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "not_clippable",
                "message": "Only a video or audio asset that owns a file can be cut into a sub-video",
            },
        )
    if payload.out_point <= payload.in_point:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "bad_request", "message": "The out point must be after the in point"},
        )
    _require_no_active_extraction(session, asset.id)

    job_payload = json.dumps(
        {
            "mode": "extract",
            "in_point": payload.in_point,
            "out_point": payload.out_point,
            "name": payload.name,
        }
    )
    job = enrichment_jobs.submit(session, asset, KIND_EXTRACT_SUBVIDEO, payload=job_payload)
    return DataResponse(data=KINDS["enrichment"].to_activity(job))


@router.post("/{clip_id}/promote", response_model=DataResponse[ActivityJobRead], status_code=202)
def promote_clip(
    clip_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[ActivityJobRead]:
    """Turn a live clip into a standalone sub-video, in place.

    The one-click path the parent-delete guard offers: promote every dependent clip,
    then retry the delete. `job.asset_id` is the clip itself throughout — nothing new
    is created, so unlike `subvideo` there is no `result_asset_id` to look for.
    """
    clip = _owned_asset(clip_id, user.id, session)
    if clip.storage_key is not None or not clip.parent_asset_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "not_a_clip", "message": "This asset is not a clip"},
        )
    _require_no_active_extraction(session, clip.id)

    job = enrichment_jobs.submit(
        session, clip, KIND_EXTRACT_SUBVIDEO, payload=json.dumps({"mode": "promote"})
    )
    return DataResponse(data=KINDS["enrichment"].to_activity(job))
