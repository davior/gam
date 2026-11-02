"""Each user's default model per generation kind.

Kept by fal endpoint id rather than by catalogue row id: an endpoint id means the same
thing on every install and survives an admin deleting and re-adding a row, and it is
what a caller outside GAM (Video Creator's per-project choice) can name without having
read this install's catalogue first.

A default whose row is gone or switched off reads as no default, never as an error: the
catalogue changes under every user's feet, and a stale preference should fall back to
the catalogue's own order rather than break every form.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session, col, select

from app.models.generation import GENERATION_KINDS, GenerationModel
from app.settings_store import DEFAULT_GENERATION_MODELS, get_setting, set_setting


class InvalidDefault(ValueError):
    """A default that cannot be saved. The message says which kind and why."""


def stored(session: Session, user_id: str) -> dict[str, str]:
    """The user's defaults as saved, with anything malformed dropped."""
    value = get_setting(session, user_id, DEFAULT_GENERATION_MODELS, {})
    if not isinstance(value, dict):
        return {}
    return {
        kind: endpoint
        for kind, endpoint in value.items()
        if kind in GENERATION_KINDS and isinstance(endpoint, str) and endpoint
    }


def update(session: Session, user_id: str, changes: dict[str, Optional[str]]) -> dict[str, str]:
    """Apply a PATCH-like change: a kind set to an endpoint id, or to null/"" to clear it.

    Every kind is checked before anything is written, so one bad entry saves nothing.
    """
    current = stored(session, user_id)
    for kind, endpoint in changes.items():
        if kind not in GENERATION_KINDS:
            raise InvalidDefault(f"There is no kind of generation called {kind!r}")
        if endpoint is None or (isinstance(endpoint, str) and not endpoint.strip()):
            current.pop(kind, None)
            continue
        if not isinstance(endpoint, str):
            raise InvalidDefault(f"The default for {kind} must be a model's endpoint id")
        endpoint = endpoint.strip()
        row = session.exec(
            select(GenerationModel).where(GenerationModel.endpoint_id == endpoint)
        ).first()
        if row is None or not row.is_active:
            raise InvalidDefault(f"{endpoint} is not an active model in the catalogue")
        if row.kind != kind:
            raise InvalidDefault(f"{row.label} is not a {kind.replace('_', ' ')} model")
        current[kind] = endpoint
    set_setting(session, user_id, DEFAULT_GENERATION_MODELS, current)
    return current


def ordered(rows: list[GenerationModel], defaults: dict[str, str]) -> list[GenerationModel]:
    """The rows with each kind's default moved to the front of its kind, order otherwise
    kept. Moving it, rather than only flagging it, is what makes a caller that takes the
    first model of a kind (Video Creator, and anything older than the flag) follow the
    user's choice without knowing the setting exists."""
    def key(item: tuple[int, GenerationModel]) -> tuple[Any, ...]:
        index, row = item
        return (row.kind, 0 if defaults.get(row.kind) == row.endpoint_id else 1, index)

    return [row for _index, row in sorted(enumerate(rows), key=key)]


def active_defaults(session: Session, user_id: str) -> dict[str, str]:
    """The stored defaults that still name an active row of the right kind."""
    wanted = stored(session, user_id)
    if not wanted:
        return {}
    rows = session.exec(
        select(GenerationModel).where(
            col(GenerationModel.endpoint_id).in_(list(wanted.values())),
            col(GenerationModel.is_active).is_(True),
        )
    ).all()
    live = {(row.kind, row.endpoint_id) for row in rows}
    return {kind: endpoint for kind, endpoint in wanted.items() if (kind, endpoint) in live}
