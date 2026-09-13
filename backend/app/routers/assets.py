"""The asset library API."""

from __future__ import annotations

import logging
from typing import AsyncIterator, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlmodel import Session, col, func, select

from app.auth import CurrentUser
from app.database import get_session
from app.ingest.filetypes import ASSET_TYPES
from app.models.asset import Asset
from app.schemas import DataResponse, ListResponse
from app.schemas_assets import AssetRead, AssetUpdate, UploadRejection, UploadResult
from app.services import assets as service
from app.storage import LocalStorage, build_storage

router = APIRouter()

logger = logging.getLogger(__name__)

# Read in 1 MiB chunks so memory stays flat regardless of file size — this app is
# expected to take multi-gigabyte video.
UPLOAD_CHUNK_SIZE = 1024 * 1024

MAX_PAGE_SIZE = 200


def get_storage() -> LocalStorage:
    return build_storage()


async def _stream(upload: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await upload.read(UPLOAD_CHUNK_SIZE):
        yield chunk


def _owned(asset_id: str, user_id: str, session: Session) -> Asset:
    asset = session.get(Asset, asset_id)
    # A row belonging to someone else is reported as absent, not as forbidden: telling
    # a caller that an id exists but is not theirs is itself a disclosure.
    if not asset or asset.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "No such asset"},
        )
    return asset


# ─── upload ──────────────────────────────────────────────────────────────────


@router.post("", response_model=UploadResult, status_code=status.HTTP_201_CREATED)
async def upload_assets(
    user: CurrentUser,
    files: List[UploadFile] = File(...),
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> UploadResult:
    """Store one or more files.

    Partial success by design (FR 6.1.1, 6.1.4): a batch of twenty where one is
    unsupported stores nineteen and reports the twentieth, rather than refusing all of
    them. A user who dragged in a folder should not have to find the offending file
    themselves.
    """
    created: list[AssetRead] = []
    rejected: list[UploadRejection] = []

    for upload in files:
        filename = upload.filename or "unnamed"
        try:
            asset = await service.ingest_upload(
                session,
                storage,
                user_id=user.id,
                filename=filename,
                chunks=_stream(upload),
                content_type=upload.content_type,
            )
            created.append(service.to_read_model(asset, storage))
        except service.UnsupportedFile:
            rejected.append(
                UploadRejection(
                    filename=filename,
                    code="unsupported_type",
                    message="That file type is not supported",
                )
            )
        except Exception as exc:  # noqa: BLE001 - one bad file must not fail the batch
            logger.exception("Upload failed for %s", filename)
            rejected.append(
                UploadRejection(
                    filename=filename,
                    code="upload_failed",
                    message=f"Could not store this file: {type(exc).__name__}",
                )
            )
        finally:
            await upload.close()

    if not created and rejected:
        # Nothing stored at all is a failed request, not a 201 describing an empty
        # result — the client should be able to tell without inspecting the body.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": rejected[0].code,
                "message": rejected[0].message
                if len(rejected) == 1
                else f"None of the {len(rejected)} files could be stored",
            },
        )

    return UploadResult(created=created, rejected=rejected)


# ─── reading ─────────────────────────────────────────────────────────────────


@router.get("", response_model=ListResponse[AssetRead])
def list_assets(
    user: CurrentUser,
    asset_type: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> ListResponse[AssetRead]:
    """The library, newest first.

    `q` is a substring match for now — real search (FTS5 and embeddings) is M5. It is
    here because a library you cannot filter at all is unusable well before then.
    """
    if asset_type and asset_type not in ASSET_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "bad_request", "message": f"Unknown asset type: {asset_type}"},
        )

    filters = [Asset.user_id == user.id]
    if asset_type:
        filters.append(Asset.asset_type == asset_type)
    if q and q.strip():
        term = f"%{q.strip()}%"
        filters.append(
            col(Asset.name).ilike(term)
            | col(Asset.description).ilike(term)
            | col(Asset.original_name).ilike(term)
        )

    total = session.exec(select(func.count()).select_from(Asset).where(*filters)).one()

    rows = session.exec(
        select(Asset)
        .where(*filters)
        .order_by(col(Asset.upload_date).desc(), col(Asset.id).desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return ListResponse[AssetRead](
        data=[service.to_read_model(row, storage) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{asset_id}", response_model=DataResponse[AssetRead])
def get_asset(
    asset_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> DataResponse[AssetRead]:
    asset = _owned(asset_id, user.id, session)
    return DataResponse(data=service.to_read_model(asset, storage))


# ─── editing ─────────────────────────────────────────────────────────────────


@router.patch("/{asset_id}", response_model=DataResponse[AssetRead])
def update_asset(
    asset_id: str,
    payload: AssetUpdate,
    user: CurrentUser,
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> DataResponse[AssetRead]:
    """Edit metadata by hand (FR 7.1.3).

    `exclude_unset` is what distinguishes "clear this description" from "leave it
    alone" — both arrive as a body without a useful value otherwise.
    """
    asset = _owned(asset_id, user.id, session)
    asset = service.apply_metadata(session, asset, payload.model_dump(exclude_unset=True))
    return DataResponse(data=service.to_read_model(asset, storage))


# response_model=None is required alongside the `-> None` annotation. FastAPI infers a
# response model from the return type, and `NoneType` is a type like any other, so it
# would try to give a 204 a body and refuse. Passing None explicitly turns inference off.
@router.delete(
    "/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
def delete_asset(
    asset_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> None:
    asset = _owned(asset_id, user.id, session)
    service.delete_asset(session, storage, asset)
