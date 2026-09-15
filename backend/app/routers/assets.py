"""The asset library API."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import AsyncIterator, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlmodel import Session, col, func, select

from app.auth import CurrentUser
from app.database import get_session
from app.ingest.filetypes import ASSET_TYPES
from app.models.asset import Asset
from app.models.tag import Tag
from app.schemas import DataResponse, ListResponse
from app.schemas_assets import AssetRead, AssetUpdate, UploadRejection, UploadResult
from app.schemas_tags import AssetTagsWrite, BulkTagsResult, BulkTagsWrite, TagRead
from app.services import assets as service
from app.services import tags as tag_service
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
    tag: List[str] = Query(default_factory=list),
    category_id: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None, max_length=50),
    min_duration: Optional[float] = Query(default=None, ge=0),
    max_duration: Optional[float] = Query(default=None, ge=0),
    uploaded_after: Optional[datetime] = Query(default=None),
    uploaded_before: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> ListResponse[AssetRead]:
    """The library, newest first, narrowed by whatever the caller asked for.

    `q` is a substring match. Real search is `/api/search` (M5); this stays because the
    library grid filters as you type against the rows it is already showing, which is a
    different job from ranked retrieval.

    Every filter here narrows. `tag` repeats and is ANDed — someone who picks two tags
    wants the assets that are both, and an OR would hand back a longer list than they
    started with, which is the opposite of what pressing a filter is for.
    """
    if asset_type and asset_type not in ASSET_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "bad_request", "message": f"Unknown asset type: {asset_type}"},
        )
    if min_duration is not None and max_duration is not None and min_duration > max_duration:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "bad_request",
                "message": "min_duration cannot be greater than max_duration",
            },
        )

    filters = [Asset.user_id == user.id]
    if asset_type:
        filters.append(Asset.asset_type == asset_type)
    if source:
        filters.append(Asset.source == source)
    if min_duration is not None:
        filters.append(col(Asset.duration_seconds) >= min_duration)
    if max_duration is not None:
        filters.append(col(Asset.duration_seconds) <= max_duration)
    if uploaded_after is not None:
        filters.append(col(Asset.upload_date) >= uploaded_after)
    if uploaded_before is not None:
        filters.append(col(Asset.upload_date) <= uploaded_before)
    if q and q.strip():
        term = f"%{q.strip()}%"
        filters.append(
            col(Asset.name).ilike(term)
            | col(Asset.description).ilike(term)
            | col(Asset.original_name).ilike(term)
        )

    # Tag and category filters resolve to a set of ids first. Both are questions about
    # the join table rather than about the asset row, and an id set keeps them from
    # turning the main query into a pile of correlated subqueries — one per tag, in the
    # AND case.
    if tag:
        matched = tag_service.asset_ids_with_all_tags(session, user.id, tag)
        if not matched:
            return _empty_page(limit, offset)
        filters.append(col(Asset.id).in_(matched))
    if category_id:
        in_category = tag_service.asset_ids_in_category(session, user.id, category_id)
        if not in_category:
            return _empty_page(limit, offset)
        filters.append(col(Asset.id).in_(in_category))

    total = session.exec(select(func.count()).select_from(Asset).where(*filters)).one()

    rows = session.exec(
        select(Asset)
        .where(*filters)
        .order_by(col(Asset.upload_date).desc(), col(Asset.id).desc())
        .limit(limit)
        .offset(offset)
    ).all()

    # One query for the whole page's tags, not one per row.
    tags_by_asset = tag_service.tags_for_many(session, [row.id for row in rows])

    return ListResponse[AssetRead](
        data=[
            service.to_read_model(row, storage, tags_by_asset.get(row.id, []))
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


def _empty_page(limit: int, offset: int) -> ListResponse[AssetRead]:
    """No asset can match, so say so without asking the database again.

    An unknown tag or an empty category is not an error — it is a filter that excludes
    everything, and `IN ()` is both awkward to build and pointless to run.
    """
    return ListResponse[AssetRead](data=[], total=0, limit=limit, offset=offset)


@router.get("/{asset_id}", response_model=DataResponse[AssetRead])
def get_asset(
    asset_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> DataResponse[AssetRead]:
    asset = _owned(asset_id, user.id, session)
    # Tags loaded explicitly, like the list endpoint does. `to_read_model` defaults them
    # to empty, so omitting this does not fail — it silently returns an untagged asset,
    # and the store believes it.
    return DataResponse(
        data=service.to_read_model(asset, storage, tag_service.tags_for(session, asset.id))
    )


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
    # Same reason as the read above, and it bites harder here: the library store replaces
    # its copy with whatever this returns, so a response with empty tags makes an
    # asset's tags disappear from the grid after a rename until the page is reloaded.
    return DataResponse(
        data=service.to_read_model(asset, storage, tag_service.tags_for(session, asset.id))
    )


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


# ─── tagging ─────────────────────────────────────────────────────────────────


def _tag_read(tag) -> TagRead:
    return TagRead(id=tag.id, name=tag.name, category_id=tag.category_id)


@router.post("/{asset_id}/tags", response_model=ListResponse[TagRead])
def add_asset_tags(
    asset_id: str,
    payload: AssetTagsWrite,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> ListResponse[TagRead]:
    """Attach tags by name, creating any that do not exist yet.

    By name rather than by id because the tag box does not know whether what was typed
    exists — making the client resolve that first turns one interaction into two round
    trips and a race where two tabs both create the same tag.

    Returns the asset's full tag set rather than what was added, so the client can
    replace its state instead of reconciling it.
    """
    asset = _owned(asset_id, user.id, session)

    for name in payload.names:
        tag = tag_service.get_or_create(session, user.id, name)
        if tag is not None:
            tag_service.attach(session, asset.id, tag.id)

    service.reindex_ids(session, [asset.id])
    return _current_tags(session, asset.id)


@router.delete("/{asset_id}/tags/{tag_id}", response_model=ListResponse[TagRead])
def remove_asset_tag(
    asset_id: str,
    tag_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> ListResponse[TagRead]:
    """Detach a tag from this asset. The tag itself survives — it is still in the
    vocabulary, and other assets may carry it."""
    asset = _owned(asset_id, user.id, session)
    tag = session.get(Tag, tag_id)
    if tag is None or tag.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Tag not found"},
        )

    tag_service.detach(session, asset.id, tag.id)
    service.reindex_ids(session, [asset.id])
    return _current_tags(session, asset.id)


def _current_tags(session: Session, asset_id: str) -> ListResponse[TagRead]:
    rows = tag_service.tags_for(session, asset_id)
    return ListResponse[TagRead](
        data=[_tag_read(tag) for tag in rows], total=len(rows), limit=len(rows), offset=0
    )


@router.post("/tags/bulk", response_model=DataResponse[BulkTagsResult])
def bulk_tag_assets(
    payload: BulkTagsWrite,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[BulkTagsResult]:
    """Apply and remove tags across a selection in one call.

    Ownership is filtered first and the filtered list is what everything downstream uses,
    so ids belonging to somebody else are dropped rather than acted on — a selection
    arrives from the client and is not evidence of anything. `updated` reports what was
    actually touched, which is how a caller notices the difference.

    Adds are applied before removes: asking for both on the same tag is contradictory,
    and removing last means the result matches what the user last clicked.
    """
    owned = tag_service.owned_asset_ids(session, user.id, payload.asset_ids)
    if not owned:
        return DataResponse[BulkTagsResult](data=BulkTagsResult(updated=0))

    added = []
    for name in payload.add:
        tag = tag_service.get_or_create(session, user.id, name)
        if tag is not None:
            added.append(tag)

    for asset_id in owned:
        for tag in added:
            tag_service.attach(session, asset_id, tag.id)
        for tag_id in payload.remove:
            tag_service.detach(session, asset_id, tag_id)

    service.reindex_ids(session, owned)

    return DataResponse[BulkTagsResult](
        data=BulkTagsResult(updated=len(owned), tags_added=[_tag_read(t) for t in added])
    )
