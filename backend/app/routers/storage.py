"""How much disk GAM uses, how much is left, and clearing what nothing refers to.

Two audiences. Anybody signed in sees their own library's share — what they could free
by deleting things. The server's disk, every user's total and the stray-file sweep are
for administrators: how full one person's server is, and what everybody else keeps on
it, is not every account's business.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.auth import AdminUser, CurrentUser, is_admin
from app.database import get_session
from app.models.user import User
from app.routers.assets import get_storage
from app.schemas import DataResponse
from app.schemas_storage import CleanupResult, StorageStatus, StorageSummary
from app.services import storage_usage
from app.storage import LocalStorage, capacity

router = APIRouter()


@router.get("", response_model=DataResponse[StorageSummary])
def read_storage(
    user: CurrentUser,
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> DataResponse[StorageSummary]:
    admin = is_admin(user, session.get(User, user.id))
    volumes = capacity.volumes()
    return DataResponse(
        data=StorageSummary(
            level=storage_usage.level(volumes),
            library=storage_usage.library_usage(session, storage, user.id),
            server=storage_usage.server_usage(session, storage, volumes) if admin else None,
        )
    )


@router.get("/status", response_model=DataResponse[StorageStatus])
def read_status(
    user: CurrentUser, session: Session = Depends(get_session)
) -> DataResponse[StorageStatus]:
    """What the header polls. `statvfs` only — no walk, no table scan.

    The level is everybody's, because a refused upload is everybody's problem and the
    warning is what explains it before it happens. The numbers are an admin's.
    """
    volumes = capacity.volumes()
    level = storage_usage.level(volumes)
    tightest = capacity.tightest(volumes)
    if tightest is None or not is_admin(user, session.get(User, user.id)):
        return DataResponse(data=StorageStatus(level=level))
    return DataResponse(
        data=StorageStatus(
            level=level, free_bytes=tightest.free_bytes, total_bytes=tightest.total_bytes
        )
    )


@router.post("/cleanup", response_model=DataResponse[CleanupResult])
def clean_up_strays(
    user: AdminUser,
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> DataResponse[CleanupResult]:
    return DataResponse(data=storage_usage.remove_strays(session, storage))
