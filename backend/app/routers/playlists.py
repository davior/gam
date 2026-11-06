"""Playlists: named, ordered runs of assets and moments, played one after another.

The rules live in services/playlists.py, which the assistant's Apply goes through too.
`GET /{id}` is also what Video Creator reads to build a video from a playlist
(docs/playlists.md), so its item shape is a contract with another app.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session, col, select

from app.auth import CurrentUser
from app.database import get_session
from app.models.playlist import Playlist, PlaylistItem
from app.routers.assets import get_storage
from app.schemas import DataResponse, ListResponse
from app.schemas_playlists import (
    PlaylistCreate,
    PlaylistDetail,
    PlaylistItemIn,
    PlaylistItemRead,
    PlaylistItemsAdd,
    PlaylistItemUpdate,
    PlaylistOrder,
    PlaylistRead,
    PlaylistUpdate,
)
from app.services import playlists as service
from app.services.assets import signed_url
from app.storage import LocalStorage

router = APIRouter()

STATUS = {"not_found": 404, "stale_order": 409, "too_many_items": 400}


def _error(code: int, name: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": name, "message": message})


def _problem(exc: service.PlaylistProblem) -> HTTPException:
    # An item naming a missing asset is a bad request about that item, not a missing
    # playlist: the playlist is there, the body is wrong.
    code = 400 if exc.code == "not_found" else STATUS.get(exc.code, 400)
    return _error(code, exc.code, exc.message)


def _owned(session: Session, user_id: str, playlist_id: str) -> Playlist:
    row = service.owned(session, user_id, playlist_id)
    if row is None:
        raise _error(404, "not_found", "No such playlist.")
    return row


def _owned_item(session: Session, playlist: Playlist, item_id: str) -> PlaylistItem:
    item = session.get(PlaylistItem, item_id)
    if item is None or item.playlist_id != playlist.id:
        raise _error(404, "not_found", "No such item in this playlist.")
    return item


def _spec(item: PlaylistItemIn) -> service.ItemSpec:
    return service.ItemSpec(**item.model_dump())


def _read(playlist: Playlist, summary: dict) -> PlaylistRead:
    first = summary.get("first")
    return PlaylistRead(
        id=playlist.id, name=playlist.name, description=playlist.description,
        item_count=summary.get("count", 0), total_seconds=round(summary.get("seconds", 0.0), 3),
        thumb_url=signed_url(first.thumb_key) if first is not None else None,
        created_at=playlist.created_at, updated_at=playlist.updated_at)


def _detail(session: Session, storage: LocalStorage, playlist: Playlist) -> PlaylistDetail:
    items = service.items_of(session, playlist.id)
    assets = service.assets_for(session, items)
    reads: List[PlaylistItemRead] = []
    for position, item in enumerate(items):
        asset = assets.get(item.asset_id)
        if asset is None:  # The foreign key forbids it; a read should not crash if it happens.
            continue
        start, end = service.effective_range(item, asset)
        reads.append(PlaylistItemRead(
            id=item.id, position=position, asset_id=asset.id, asset_name=asset.name,
            asset_type=asset.asset_type, in_point=item.in_point, out_point=item.out_point,
            start=start, end=end, image_seconds=item.image_seconds, label=item.label,
            note=item.note, asset_duration_seconds=asset.duration_seconds,
            mime_type=asset.mime_type, width=asset.width, height=asset.height,
            file_url=signed_url(asset.storage_key), thumb_url=signed_url(asset.thumb_key),
            missing=bool(asset.storage_key) and not storage.stat(asset.storage_key).exists))
    summary = service.summaries(session, [playlist])[playlist.id]
    return PlaylistDetail(**_read(playlist, summary).model_dump(), items=reads)


@router.get("", response_model=ListResponse[PlaylistRead])
def list_playlists(user: CurrentUser, limit: int = 100, offset: int = 0,
                   session: Session = Depends(get_session)) -> ListResponse[PlaylistRead]:
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    rows = session.exec(select(Playlist).where(Playlist.user_id == user.id)
                        .order_by(col(Playlist.updated_at).desc())
                        .offset(offset).limit(limit)).all()
    summaries = service.summaries(session, rows)
    return ListResponse(data=[_read(row, summaries[row.id]) for row in rows],
                        total=service.count_for(session, user.id), limit=limit, offset=offset)


@router.post("", response_model=DataResponse[PlaylistDetail], status_code=201)
def create_playlist(body: PlaylistCreate, user: CurrentUser,
                    session: Session = Depends(get_session),
                    storage: LocalStorage = Depends(get_storage)) -> DataResponse[PlaylistDetail]:
    try:
        playlist = service.create(session, user.id, body.name, body.description,
                                  [_spec(i) for i in body.items])
    except service.PlaylistProblem as exc:
        raise _problem(exc) from exc
    return DataResponse(data=_detail(session, storage, playlist))


@router.get("/{playlist_id}", response_model=DataResponse[PlaylistDetail])
def get_playlist(playlist_id: str, user: CurrentUser, session: Session = Depends(get_session),
                 storage: LocalStorage = Depends(get_storage)) -> DataResponse[PlaylistDetail]:
    return DataResponse(data=_detail(session, storage, _owned(session, user.id, playlist_id)))


@router.patch("/{playlist_id}", response_model=DataResponse[PlaylistDetail])
def update_playlist(playlist_id: str, body: PlaylistUpdate, user: CurrentUser,
                    session: Session = Depends(get_session),
                    storage: LocalStorage = Depends(get_storage)) -> DataResponse[PlaylistDetail]:
    playlist = _owned(session, user.id, playlist_id)
    changes = {field: getattr(body, field) for field in body.model_fields_set}
    try:
        service.rename(session, playlist, **changes)
    except service.PlaylistProblem as exc:
        raise _problem(exc) from exc
    return DataResponse(data=_detail(session, storage, playlist))


@router.delete("/{playlist_id}", status_code=204)
def delete_playlist(playlist_id: str, user: CurrentUser,
                    session: Session = Depends(get_session)) -> Response:
    service.delete_playlist(session, _owned(session, user.id, playlist_id))
    return Response(status_code=204)


@router.post("/{playlist_id}/items", response_model=DataResponse[PlaylistDetail], status_code=201)
def add_items(playlist_id: str, body: PlaylistItemsAdd, user: CurrentUser,
              session: Session = Depends(get_session),
              storage: LocalStorage = Depends(get_storage)) -> DataResponse[PlaylistDetail]:
    playlist = _owned(session, user.id, playlist_id)
    try:
        service.add(session, playlist, [_spec(i) for i in body.items], body.position)
    except service.PlaylistProblem as exc:
        raise _problem(exc) from exc
    return DataResponse(data=_detail(session, storage, playlist))


@router.patch("/{playlist_id}/items/{item_id}", response_model=DataResponse[PlaylistDetail])
def update_item(playlist_id: str, item_id: str, body: PlaylistItemUpdate, user: CurrentUser,
                session: Session = Depends(get_session),
                storage: LocalStorage = Depends(get_storage)) -> DataResponse[PlaylistDetail]:
    playlist = _owned(session, user.id, playlist_id)
    item = _owned_item(session, playlist, item_id)
    changes = {field: getattr(body, field) for field in body.model_fields_set}
    try:
        service.update_item(session, playlist, item, **changes)
    except service.PlaylistProblem as exc:
        session.rollback()
        raise _problem(exc) from exc
    return DataResponse(data=_detail(session, storage, playlist))


@router.delete("/{playlist_id}/items/{item_id}", response_model=DataResponse[PlaylistDetail])
def remove_item(playlist_id: str, item_id: str, user: CurrentUser,
                session: Session = Depends(get_session),
                storage: LocalStorage = Depends(get_storage)) -> DataResponse[PlaylistDetail]:
    playlist = _owned(session, user.id, playlist_id)
    service.remove_item(session, playlist, _owned_item(session, playlist, item_id))
    return DataResponse(data=_detail(session, storage, playlist))


@router.put("/{playlist_id}/order", response_model=DataResponse[PlaylistDetail])
def reorder(playlist_id: str, body: PlaylistOrder, user: CurrentUser,
            session: Session = Depends(get_session),
            storage: LocalStorage = Depends(get_storage)) -> DataResponse[PlaylistDetail]:
    playlist = _owned(session, user.id, playlist_id)
    try:
        service.reorder(session, playlist, body.item_ids)
    except service.PlaylistProblem as exc:
        raise _problem(exc) from exc
    return DataResponse(data=_detail(session, storage, playlist))
