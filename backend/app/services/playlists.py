"""Playlists: what may go in one, and the reads and writes every caller shares.

The REST router and the assistant's Apply both come through here, so a playlist the
assistant made is held to exactly the rules of one a person built by hand.

The rules, each for a reason:
- Only what can be played: video, audio and images. A document has nothing to play.
- An item always names an asset that owns its file. A live clip is stored as its parent
  and its window (see models/playlist.py): Video Creator cuts from a file, and a range
  on a clip would be a range on a range.
- A range is checked by `assets.cut_problem`, the rule a clip is held to, so a moment
  that could not be clipped cannot be put in a playlist either.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from sqlmodel import Session, col, delete, func, select

from app.clock import utcnow
from app.ingest.filetypes import TYPE_AUDIO, TYPE_IMAGE, TYPE_VIDEO
from app.models.asset import Asset
from app.models.playlist import Playlist, PlaylistItem
from app.services import assets as asset_service

PLAYABLE = (TYPE_VIDEO, TYPE_AUDIO, TYPE_IMAGE)
# How long an image stays up when its item does not say.
IMAGE_SECONDS = 5.0
IMAGE_SECONDS_MIN, IMAGE_SECONDS_MAX = 0.5, 3600.0
# A playlist is something to sit through, not an export of the library. Well past any
# real use, and low enough that one page load stays one cheap query.
MAX_ITEMS = 500
NAME_MAX, LABEL_MAX, NOTE_MAX, DESCRIPTION_MAX = 255, 255, 500, 5_000


class PlaylistProblem(ValueError):
    """A request that cannot become a row, with a code and a sentence for the API."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Resolved:
    """An item as it will be stored: the file-owning asset and the range on its file."""

    asset: Asset
    in_point: Optional[float]
    out_point: Optional[float]


def resolve(session: Session, user_id: str, asset_id: object,
            in_point: Optional[float] = None, out_point: Optional[float] = None) -> Resolved:
    """Check one prospective item and normalise it, or raise `PlaylistProblem`.

    Someone else's asset reads as missing, as it does everywhere in this API.
    """
    asset = session.get(Asset, asset_id) if isinstance(asset_id, str) and asset_id else None
    if asset is None or asset.user_id != user_id:
        raise PlaylistProblem("not_found", "No such asset.")
    if asset.asset_type not in PLAYABLE:
        raise PlaylistProblem("not_playable",
                              f"{asset.name} is a {asset.asset_type}, which has nothing to play. "
                              "Playlists hold video, audio and images.")
    if (in_point is None) != (out_point is None):
        raise PlaylistProblem("bad_range", "Give both the start and the end, or neither.")

    if asset.storage_key is None and asset.parent_asset_id:
        # A live clip: its parent's file and its own window.
        if in_point is not None:
            raise PlaylistProblem(
                "bad_range", f"{asset.name} is a clip. Add it whole, or give the range on "
                             "the original it came from.")
        parent = session.get(Asset, asset.parent_asset_id)
        if parent is None or parent.user_id != user_id:
            raise PlaylistProblem("not_found", "The original this clip came from is gone.")
        return Resolved(parent, asset.in_point, asset.out_point)

    if asset.asset_type == TYPE_IMAGE:
        if in_point is not None:
            raise PlaylistProblem("bad_range", "An image has no range to play; set how long it shows.")
        return Resolved(asset, None, None)

    if in_point is not None:
        problem = asset_service.cut_problem(asset, float(in_point), float(out_point))
        if problem:
            raise PlaylistProblem("bad_range", problem)
        return Resolved(asset, float(in_point), float(out_point))
    return Resolved(asset, None, None)


def _image_seconds(asset: Asset, value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if asset.asset_type != TYPE_IMAGE:
        raise PlaylistProblem("bad_request", "Only an image has a time to show for.")
    if not IMAGE_SECONDS_MIN <= value <= IMAGE_SECONDS_MAX:
        raise PlaylistProblem("bad_request",
                              f"An image shows for {IMAGE_SECONDS_MIN:g} to {IMAGE_SECONDS_MAX:g} seconds.")
    return float(value)


def _text(value: Optional[str], limit: int) -> Optional[str]:
    value = value.strip() if isinstance(value, str) else None
    return value[:limit] if value else None


@dataclass
class ItemSpec:
    """What a caller asks to add. Plain data, so the router and the assistant share it."""

    asset_id: str
    in_point: Optional[float] = None
    out_point: Optional[float] = None
    image_seconds: Optional[float] = None
    label: Optional[str] = None
    note: Optional[str] = None


def owned(session: Session, user_id: str, playlist_id: object) -> Optional[Playlist]:
    row = session.get(Playlist, playlist_id) if isinstance(playlist_id, str) and playlist_id else None
    return row if row is not None and row.user_id == user_id else None


def items_of(session: Session, playlist_id: str) -> List[PlaylistItem]:
    return list(session.exec(
        select(PlaylistItem).where(PlaylistItem.playlist_id == playlist_id)
        .order_by(col(PlaylistItem.position), col(PlaylistItem.created_at))).all())


def _build(session: Session, user_id: str, playlist_id: str,
           specs: Sequence[ItemSpec]) -> List[PlaylistItem]:
    """Every spec checked before any row is made, so a bad tenth item adds none of them."""
    rows = []
    for spec in specs:
        resolved = resolve(session, user_id, spec.asset_id, spec.in_point, spec.out_point)
        rows.append(PlaylistItem(
            playlist_id=playlist_id, asset_id=resolved.asset.id,
            in_point=resolved.in_point, out_point=resolved.out_point,
            image_seconds=_image_seconds(resolved.asset, spec.image_seconds),
            label=_text(spec.label, LABEL_MAX), note=_text(spec.note, NOTE_MAX)))
    return rows


def _name(value: Optional[str]) -> str:
    name = (value or "").strip()
    if not name:
        raise PlaylistProblem("bad_request", "A playlist needs a name.")
    if len(name) > NAME_MAX:
        raise PlaylistProblem("bad_request", f"The name is too long (at most {NAME_MAX} characters).")
    return name


def create(session: Session, user_id: str, name: str, description: Optional[str] = None,
           specs: Sequence[ItemSpec] = ()) -> Playlist:
    playlist = Playlist(user_id=user_id, name=_name(name),
                        description=_text(description, DESCRIPTION_MAX))
    if len(specs) > MAX_ITEMS:
        raise PlaylistProblem("too_many_items", f"A playlist holds at most {MAX_ITEMS} items.")
    rows = _build(session, user_id, playlist.id, specs)
    for position, row in enumerate(rows):
        row.position = position
    session.add(playlist)
    # The playlist first: `playlist_item.playlist_id` is a foreign key, and nothing tells
    # the unit of work that the rows depend on it.
    session.flush()
    session.add_all(rows)
    session.commit()
    session.refresh(playlist)
    return playlist


def add(session: Session, playlist: Playlist, specs: Sequence[ItemSpec],
        position: Optional[int] = None) -> List[PlaylistItem]:
    """Insert items at `position` (the end when None), keeping the order dense."""
    current = items_of(session, playlist.id)
    if len(current) + len(specs) > MAX_ITEMS:
        raise PlaylistProblem("too_many_items", f"A playlist holds at most {MAX_ITEMS} items.")
    rows = _build(session, playlist.user_id, playlist.id, specs)
    at = len(current) if position is None else max(0, min(position, len(current)))
    ordered = current[:at] + rows + current[at:]
    for index, row in enumerate(ordered):
        row.position = index
    session.add_all(ordered)
    _touch(session, playlist)
    session.commit()
    for row in rows:
        session.refresh(row)
    return rows


_UNSET = object()


def update_item(session: Session, playlist: Playlist, item: PlaylistItem, *,
                in_point=_UNSET, out_point=_UNSET, image_seconds=_UNSET,
                label=_UNSET, note=_UNSET) -> PlaylistItem:
    """Change what was given. A range is given as a pair; (None, None) means the whole asset."""
    asset = session.get(Asset, item.asset_id)
    if in_point is not _UNSET or out_point is not _UNSET:
        start = item.in_point if in_point is _UNSET else in_point
        end = item.out_point if out_point is _UNSET else out_point
        resolved = resolve(session, playlist.user_id, item.asset_id, start, end)
        item.in_point, item.out_point = resolved.in_point, resolved.out_point
    if image_seconds is not _UNSET:
        item.image_seconds = _image_seconds(asset, image_seconds)
    if label is not _UNSET:
        item.label = _text(label, LABEL_MAX)
    if note is not _UNSET:
        item.note = _text(note, NOTE_MAX)
    item.updated_at = utcnow()
    session.add(item)
    _touch(session, playlist)
    session.commit()
    session.refresh(item)
    return item


def remove_item(session: Session, playlist: Playlist, item: PlaylistItem) -> None:
    session.delete(item)
    session.flush()
    for index, row in enumerate(items_of(session, playlist.id)):
        row.position = index
        session.add(row)
    _touch(session, playlist)
    session.commit()


def reorder(session: Session, playlist: Playlist, item_ids: Sequence[str]) -> None:
    """Put the items in this order. Must name every item exactly once: a partial list
    would leave the rest with no defined place, and a stale one would quietly drop an item
    added in another tab."""
    current = {row.id: row for row in items_of(session, playlist.id)}
    if len(item_ids) != len(current) or set(item_ids) != set(current):
        raise PlaylistProblem("stale_order",
                              "The playlist has changed since it was loaded. Reload and try again.")
    for index, item_id in enumerate(item_ids):
        current[item_id].position = index
        session.add(current[item_id])
    _touch(session, playlist)
    session.commit()


def rename(session: Session, playlist: Playlist, *, name=_UNSET, description=_UNSET) -> Playlist:
    if name is not _UNSET:
        playlist.name = _name(name)
    if description is not _UNSET:
        playlist.description = _text(description, DESCRIPTION_MAX)
    _touch(session, playlist)
    session.commit()
    session.refresh(playlist)
    return playlist


def delete_playlist(session: Session, playlist: Playlist) -> None:
    session.exec(delete(PlaylistItem).where(col(PlaylistItem.playlist_id) == playlist.id))
    session.delete(playlist)
    session.commit()


def delete_for_assets(session: Session, asset_ids: Iterable[str]) -> None:
    """Drop the items that name these assets, as part of deleting them. Does not commit:
    the caller deletes the assets in the same transaction (`assets._delete_dependents`).

    The playlists keep their remaining items in order, with a gap in `position` that the
    next edit closes; reads order by position, so a gap changes nothing anyone sees.
    """
    ids = list(asset_ids)
    if ids:
        session.exec(delete(PlaylistItem).where(col(PlaylistItem.asset_id).in_(ids)))


def _touch(session: Session, playlist: Playlist) -> None:
    playlist.updated_at = utcnow()
    session.add(playlist)


# ─── reading ─────────────────────────────────────────────────────────────────


def effective_range(item: PlaylistItem, asset: Asset) -> tuple[float, Optional[float]]:
    """Where the item starts and ends in its file. The end is None when the asset's
    length was never measured, and the player then plays to the file's own end."""
    if asset.asset_type == TYPE_IMAGE:
        return 0.0, item.image_seconds or IMAGE_SECONDS
    start = item.in_point if item.in_point is not None else 0.0
    end = item.out_point if item.out_point is not None else asset.duration_seconds
    return start, end


def assets_for(session: Session, items: Sequence[PlaylistItem]) -> dict[str, Asset]:
    ids = list({item.asset_id for item in items})
    if not ids:
        return {}
    return {a.id: a for a in session.exec(select(Asset).where(col(Asset.id).in_(ids))).all()}


def summaries(session: Session, playlists: Sequence[Playlist]) -> dict[str, dict]:
    """Count, total length and the first item's asset, for each playlist, in two queries."""
    ids = [p.id for p in playlists]
    out = {pid: {"count": 0, "seconds": 0.0, "first": None} for pid in ids}
    if not ids:
        return out
    rows = session.exec(
        select(PlaylistItem, Asset).join(Asset, col(Asset.id) == col(PlaylistItem.asset_id))
        .where(col(PlaylistItem.playlist_id).in_(ids))
        .order_by(col(PlaylistItem.playlist_id), col(PlaylistItem.position))).all()
    for item, asset in rows:
        entry = out[item.playlist_id]
        entry["count"] += 1
        if entry["first"] is None:
            entry["first"] = asset
        start, end = effective_range(item, asset)
        if end is not None:
            entry["seconds"] += max(0.0, end - start)
    return out


def count_for(session: Session, user_id: str) -> int:
    return session.exec(select(func.count()).select_from(Playlist)
                        .where(Playlist.user_id == user_id)).one()
