"""A video's standard-resolution versions: list, make, delete, and make one the original."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.enrichment.versions import SIZES, label_for, short_side_of
from app.ingest.filetypes import TYPE_VIDEO
from app.jobs import enrichment as enrichment_jobs
from app.jobs.registry import KINDS
from app.models.asset import Asset
from app.models.job import KIND_EXTRACT_SUBVIDEO, KIND_MAKE_VERSION
from app.models.version import AssetVersion
from app.routers.assets import get_storage
from app.schemas import DataResponse, ListResponse
from app.schemas_assets import AssetRead
from app.schemas_jobs import ActivityJobRead
from app.schemas_versions import VersionCreate, VersionRead
from app.services import assets as asset_service
from app.services import tags as tag_service
from app.services import versions as version_service
from app.storage import LocalStorage, capacity

router = APIRouter()


def _owned_video(asset_id: str, user_id: str, session: Session) -> Asset:
    asset = session.get(Asset, asset_id)
    if not asset or asset.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "No such asset"},
        )
    if asset.asset_type != TYPE_VIDEO or not asset.storage_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "not_versionable",
                "message": "Only a video that owns a file has versions",
            },
        )
    return asset


def _owned_version(asset: Asset, version_id: str, session: Session) -> AssetVersion:
    version = session.get(AssetVersion, version_id)
    if not version or version.asset_id != asset.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "No such version"},
        )
    return version


def _require_no_version_job(session: Session, asset_id: str) -> None:
    # One at a time per asset. It also guards `make_original`: a queued job reads the
    # original's file when it starts, and swapping the original underneath it would
    # have it encode the version that was just made the original.
    if enrichment_jobs.active_job(session, asset_id, KIND_MAKE_VERSION) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_running",
                "message": "A version of this video is already being made",
            },
        )


@router.get("/{asset_id}/versions", response_model=ListResponse[VersionRead])
def list_versions(
    asset_id: str, user: CurrentUser, session: Session = Depends(get_session)
) -> ListResponse[VersionRead]:
    asset = _owned_video(asset_id, user.id, session)
    rows = version_service.list_for(session, asset.id)
    return ListResponse(
        data=[version_service.to_read_model(row) for row in rows],
        total=len(rows),
        limit=len(rows),
        offset=0,
    )


@router.post(
    "/{asset_id}/versions", response_model=DataResponse[ActivityJobRead], status_code=202
)
def start_version(
    asset_id: str,
    payload: VersionCreate,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[ActivityJobRead]:
    """Queue one standard-resolution version. The original is untouched."""
    asset = _owned_video(asset_id, user.id, session)
    if payload.short_side not in SIZES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "bad_request",
                "message": "Choose one of " + ", ".join(label_for(s) for s in SIZES),
            },
        )
    # Down only. Scaling up adds bytes and no detail, and Video Creator already scales
    # a small source up to its frame at render time. A video with no recorded size is
    # let through: ffmpeg will still do what was asked.
    current = short_side_of(asset)
    if current is not None and payload.short_side >= current:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "not_smaller",
                "message": f"This video is already {label_for(current)} or smaller",
            },
        )
    if version_service.find(session, asset.id, payload.short_side) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "version_exists",
                "message": f"This video already has a {label_for(payload.short_side)} version",
            },
        )
    _require_no_version_job(session, asset.id)
    capacity.require_room()

    job = enrichment_jobs.submit(
        session,
        asset,
        KIND_MAKE_VERSION,
        payload=json.dumps({"short_side": payload.short_side}),
    )
    return DataResponse(data=KINDS["enrichment"].to_activity(job))


@router.delete(
    "/{asset_id}/versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
def delete_version(
    asset_id: str,
    version_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> Response:
    asset = _owned_video(asset_id, user.id, session)
    version_service.delete(session, storage, _owned_version(asset, version_id, session))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{asset_id}/versions/{version_id}/make-original", response_model=DataResponse[AssetRead])
def make_original(
    asset_id: str,
    version_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> DataResponse[AssetRead]:
    """Make this version the asset's file and delete the original it replaces.

    Irreversible, which is the point: it is how a library gets the space back. The
    frontend confirms first.
    """
    asset = _owned_video(asset_id, user.id, session)
    version = _owned_version(asset, version_id, session)
    _require_no_version_job(session, asset.id)
    # An extraction cutting from this asset reads its file when it starts, so the
    # file must not be deleted from under one that is queued or running.
    if enrichment_jobs.active_job(session, asset.id, KIND_EXTRACT_SUBVIDEO) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "extraction_in_progress",
                "message": "A sub-video is being cut from this video. Let it finish first.",
            },
        )
    asset = version_service.make_original(session, storage, asset, version)
    parent = session.get(Asset, asset.parent_asset_id) if asset.parent_asset_id else None
    return DataResponse(
        data=asset_service.to_read_model(
            asset, storage, tag_service.tags_for(session, asset.id), parent=parent
        )
    )
