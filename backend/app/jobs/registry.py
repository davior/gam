"""What kinds of background job exist, and how each is read and cancelled.

The activity API is a union over every job table, and this is the only place that
knows what those tables are. Adding a kind is one `JobKind` entry: a table, a way to
turn a row into an `ActivityJobRead`, and the queue that can stop it. Nothing else —
endpoint, store or indicator — changes.

Serialisation lives here rather than in each kind's router so that importing the
registry never drags in a router, and its dependencies, just to read a row.

Ported from gecko-notes, minus the note-lock machinery, which has no counterpart here:
nothing in GAM holds a document read-only while a job runs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Type

from sqlmodel import Session, col, select

from app.jobs.runner import ACTIVE_STATUSES, JobQueue, is_stale
from app.models.job import EnrichmentJob
from app.schemas_jobs import ActivityJobRead


class JobKind:
    """One row in the union: a table, a serialiser, and the queue that can stop it."""

    def __init__(
        self,
        key: str,
        model: Type[Any],
        to_activity: Callable[[Any], ActivityJobRead],
        queue_for: Callable[[], Optional[JobQueue]],
    ) -> None:
        self.key = key
        self.model = model
        self.to_activity = to_activity
        self.queue_for = queue_for


def _enrichment_to_activity(job: EnrichmentJob) -> ActivityJobRead:
    return ActivityJobRead(
        id=job.id,
        kind="enrichment",
        action=job.kind,
        status=job.status,
        # A row whose heartbeat stopped is reported as such rather than as live work.
        # The sweeper will mark it failed within the minute, but the indicator should
        # not claim it is running in the meantime.
        stalled=is_stale(job),
        stage=job.stage or "",
        progress=job.progress or 0,
        detail=job.detail or "",
        asset_id=job.asset_id,
        asset_name=job.asset_name or "",
        model=job.model or "",
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _enrichment_queue() -> Optional[JobQueue]:
    # Imported lazily: the worker pulls in ffmpeg helpers and the Deepgram client, and
    # the activity API has no reason to load either just to list a row.
    from app.jobs import enrichment

    return enrichment.queue()


KINDS: Dict[str, JobKind] = {
    "enrichment": JobKind(
        "enrichment", EnrichmentJob, _enrichment_to_activity, _enrichment_queue
    ),
}


def list_jobs(
    session: Session,
    user_id: str,
    *,
    active_only: bool = False,
    asset_id: Optional[str] = None,
    limit: int = 25,
) -> List[ActivityJobRead]:
    """Recent jobs for one user across every kind, newest first.

    `active_only` is what a client polls on mount to pick work back up after a reload;
    the unfiltered form is what it polls while something is running, so it can watch a
    job reach "done" instead of simply vanishing from the list.
    """
    collected: List[ActivityJobRead] = []

    for kind in KINDS.values():
        query = select(kind.model).where(kind.model.user_id == user_id)
        if active_only:
            query = query.where(kind.model.status.in_(ACTIVE_STATUSES))
        if asset_id and hasattr(kind.model, "asset_id"):
            query = query.where(kind.model.asset_id == asset_id)
        query = query.order_by(col(kind.model.created_at).desc()).limit(limit)
        collected.extend(kind.to_activity(row) for row in session.exec(query).all())

    # created_at is required on every job table, but a row missing one must not blow up
    # the sort by comparing a datetime against None.
    collected.sort(key=lambda job: job.created_at or datetime.min, reverse=True)
    return collected[:limit]


def get_job(
    session: Session, user_id: str, kind_key: str, job_id: str
) -> Optional[ActivityJobRead]:
    kind = KINDS.get(kind_key)
    if not kind:
        return None
    row = session.get(kind.model, job_id)
    if not row or row.user_id != user_id:
        return None
    return kind.to_activity(row)


def cancel_job(session: Session, user_id: str, kind_key: str, job_id: str) -> Optional[ActivityJobRead]:
    """Stop a job, if it is still stoppable.

    The row is marked immediately rather than waiting for the worker to notice: the
    user pressed cancel, and the UI should reflect that at once. The worker unwinds at
    its next checkpoint and finds the row already terminal.
    """
    from app.jobs.runner import set_fields

    kind = KINDS.get(kind_key)
    if not kind:
        return None
    row = session.get(kind.model, job_id)
    if not row or row.user_id != user_id:
        return None

    if row.status in ACTIVE_STATUSES:
        queue = kind.queue_for()
        if queue is not None:
            queue.cancel(job_id)
        set_fields(session, row, status="cancelled", stage="", detail="Cancelled")

    return kind.to_activity(row)
