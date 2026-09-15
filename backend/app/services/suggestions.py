"""Proposing, accepting and rejecting what an enrichment run came up with.

The whole point of this module is that nothing here writes to the library until a person
says so. `propose` only ever creates `Suggestion` rows; `accept` is the one function that
turns one into a real tag or a real title, and it is only ever reached from an endpoint a
user pressed.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from sqlmodel import Session, col, delete, select

from app.clock import utcnow
from app.models.asset import Asset
from app.models.suggestion import (
    KIND_TAG,
    KIND_TITLE,
    STATUS_ACCEPTED,
    STATUS_PENDING,
    STATUS_REJECTED,
    Suggestion,
)
from app.services import assets as asset_service
from app.services import tags as tag_service

logger = logging.getLogger(__name__)

# One asset's worth of suggestions should be reviewable at a glance. A model asked for
# tags will happily return thirty; past a handful the list stops being a decision and
# starts being a chore, which is how "accept all" becomes the only button anyone presses.
MAX_TAGS = 8


def pending_for(session: Session, asset_id: str) -> list[Suggestion]:
    return list(
        session.exec(
            select(Suggestion)
            .where(
                Suggestion.asset_id == asset_id,
                Suggestion.status == STATUS_PENDING,
            )
            .order_by(col(Suggestion.kind), col(Suggestion.created_at))
        ).all()
    )


def previously_rejected(session: Session, asset_id: str) -> set[str]:
    """Values this user has already declined for this asset, lowercased.

    Read by the job so a re-run does not propose the same tag a second time. Being asked
    twice about something you said no to is how a suggestion feature becomes noise.
    """
    rows = session.exec(
        select(Suggestion).where(
            Suggestion.asset_id == asset_id,
            Suggestion.status == STATUS_REJECTED,
        )
    ).all()
    return {row.value.strip().lower() for row in rows}


def propose(
    session: Session,
    asset: Asset,
    *,
    tags: Iterable[str],
    title: Optional[str],
    model: str,
) -> int:
    """Record what a run came up with, replacing whatever it proposed last time.

    Only *pending* rows are cleared. Accepted and rejected ones are decisions the user
    made and this has no business discarding them.
    """
    session.exec(
        delete(Suggestion).where(
            col(Suggestion.asset_id) == asset.id,
            col(Suggestion.status) == STATUS_PENDING,
        )
    )

    declined = previously_rejected(session, asset.id)
    existing = {t.name.strip().lower() for t in tag_service.tags_for(session, asset.id)}

    created = 0
    seen: set[str] = set()
    for raw in tags:
        name = tag_service.normalise(raw)
        key = name.lower()
        # Already on the asset, already refused, or a duplicate within this one batch.
        if not name or key in seen or key in declined or key in existing:
            continue
        seen.add(key)
        session.add(
            Suggestion(
                user_id=asset.user_id,
                asset_id=asset.id,
                kind=KIND_TAG,
                value=name,
                model=model,
            )
        )
        created += 1
        if created >= MAX_TAGS:
            break

    if title:
        proposed = title.strip()
        # Nothing to decide if it matches what the asset is already called, and a title
        # the user has already turned down should not come back.
        if proposed and proposed.lower() not in declined and proposed != asset.name:
            session.add(
                Suggestion(
                    user_id=asset.user_id,
                    asset_id=asset.id,
                    kind=KIND_TITLE,
                    value=proposed,
                    model=model,
                )
            )
            created += 1

    session.commit()
    return created


def accept(session: Session, asset: Asset, suggestion: Suggestion) -> Suggestion:
    """Apply one suggestion for real.

    A title is written through `apply_metadata`, the *human* path, which stamps
    provenance "human" — because it is: the user read it and chose it. That also means a
    later enrichment run will not quietly replace a title they approved, which is the
    behaviour FR 8.1.3 is asking for.
    """
    if suggestion.status != STATUS_PENDING:
        return suggestion

    if suggestion.kind == KIND_TAG:
        tag = tag_service.get_or_create(session, asset.user_id, suggestion.value)
        if tag is not None:
            # `get_or_create` matches case-insensitively, so accepting "NATO" when the
            # library already has "nato" attaches the existing one rather than splitting
            # the library across two tags that look identical.
            if tag_service.attach(session, asset.id, tag.id):
                asset_service.reindex_ids(session, [asset.id])
    elif suggestion.kind == KIND_TITLE:
        asset_service.apply_metadata(session, asset, {"name": suggestion.value})

    suggestion.status = STATUS_ACCEPTED
    suggestion.resolved_at = utcnow()
    session.add(suggestion)
    session.commit()
    session.refresh(suggestion)
    return suggestion


def reject(session: Session, suggestion: Suggestion) -> Suggestion:
    """Record a no, and keep it, so the next run does not ask again."""
    if suggestion.status != STATUS_PENDING:
        return suggestion

    suggestion.status = STATUS_REJECTED
    suggestion.resolved_at = utcnow()
    session.add(suggestion)
    session.commit()
    session.refresh(suggestion)
    return suggestion
