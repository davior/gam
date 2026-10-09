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

import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Type

from sqlmodel import Session, col, select

from app.jobs.runner import ACTIVE_STATUSES, JobQueue, is_stale
from app.models.job import KIND_EXTRACT_SUBVIDEO, KIND_GENERATE, KIND_IMPORT_URL, EnrichmentJob
from app.schemas_jobs import ActivityJobRead


class JobKind:
    """One row in the union: a table, a serialiser, and the queue that can stop it.

    `queue_for` is handed the row, since which queue runs it can depend on the row.
    """

    def __init__(
        self,
        key: str,
        model: Type[Any],
        to_activity: Callable[[Any], ActivityJobRead],
        queue_for: Callable[[Any], Optional[JobQueue]],
    ) -> None:
        self.key = key
        self.model = model
        self.to_activity = to_activity
        self.queue_for = queue_for


# Kinds whose payload names the asset(s) they made. Each creates rows that did not exist
# when the job was queued, so `asset_id` cannot carry them: for an extraction it stays
# pointed at the source, and an import or a generation has no asset at all until it is
# done.
_CREATES_AN_ASSET = frozenset({KIND_EXTRACT_SUBVIDEO, KIND_IMPORT_URL, KIND_GENERATE})


def _result_asset_ids(job: EnrichmentJob) -> List[str]:
    """Every asset the job created, in the order it created them.

    An extraction or an import makes one and records it as `created_asset_id`; a
    generation can make up to four and records `created_asset_ids`, appended as each is
    saved — so a running generation already reports the ones it has.

    Best-effort, the same defensive shape `routers/transcripts.py::_words_of` uses to
    read a JSON-as-TEXT column that might be empty, or — for every other kind, whose
    payload means something else entirely — simply not have this key.
    """
    if job.kind not in _CREATES_AN_ASSET or not job.payload:
        return []
    try:
        data = json.loads(job.payload)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    many = data.get("created_asset_ids")
    if isinstance(many, list):
        return [value for value in many if isinstance(value, str)]
    one = data.get("created_asset_id")
    return [one] if isinstance(one, str) else []


def _enrichment_to_activity(job: EnrichmentJob) -> ActivityJobRead:
    created = _result_asset_ids(job)
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
        result_asset_id=created[0] if created else None,
        result_asset_ids=created,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _enrichment_queue(job: EnrichmentJob) -> Optional[JobQueue]:
    # Imported lazily: the worker pulls in ffmpeg helpers and the Deepgram client, and
    # the activity API has no reason to load either just to list a row.
    from app.jobs import enrichment

    # By the row's kind, because one table now feeds two queues and a cancel sent to
    # the one not running the job would be silently ignored.
    return enrichment.queue_for(job.kind)


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
        was_queued = row.status == "queued"
        queue = kind.queue_for(row)
        if queue is not None:
            queue.cancel(job_id)
        set_fields(session, row, status="cancelled", stage="", detail="Cancelled")
        if was_queued and getattr(row, "kind", None) == KIND_GENERATE:
            # A queued generation can already have a request running at fal — one
            # recovered after a restart — and no worker will reach the checkpoint that
            # would cancel it there. Imported lazily for the reason `_enrichment_queue`
            # gives.
            from app.generation.run import cancel_remote

            cancel_remote(session, row)

    return kind.to_activity(row)
