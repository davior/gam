"""The enrichment worker: one queue, dispatching on a job's kind.

One queue rather than one per action, because they contend for the same things — CPU
for ffmpeg, and a rate-limited upstream — and separate queues would each honour their
own cap while collectively ignoring it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from app.config import settings
from app.database import engine
from app.enrichment.transcribe import TranscriptionError
from app.enrichment.transcribe import run as run_transcribe
from app.jobs.runner import (
    ACTIVE_STATUSES,
    JobCancelled,
    JobQueue,
    readable_error,
    set_fields,
)
from app.models.asset import Asset
from app.models.job import EnrichmentJob, KIND_TRANSCRIBE

logger = logging.getLogger(__name__)

_queue: Optional[JobQueue] = None


def queue() -> JobQueue:
    """The process's enrichment queue, built on first use.

    Lazily, so importing this module — which the activity API does, transitively —
    does not start threads in a test that never wanted them.
    """
    global _queue
    if _queue is None:
        _queue = JobQueue(
            EnrichmentJob,
            _run_job,
            name="enrichment",
            concurrency=settings.enrichment_concurrency,
            engine=engine,
        )
    return _queue


def start() -> None:
    queue().start()


def enqueue(job_id: str) -> None:
    queue().enqueue(job_id)


def _run_job(job_id: str) -> None:
    """Run one job to completion, recording whatever happened.

    Every exit writes a terminal status. A job that ends without one sits at
    "processing" until the stale sweeper ends it forty minutes later, which reads to
    the user as a hang.
    """
    q = queue()
    progress = q.reporter(job_id)

    with q.session() as session:
        job = session.get(EnrichmentJob, job_id)
        if not job:
            logger.warning("Enrichment job %s vanished before it ran", job_id)
            return

        if job.status == "cancelled" or q.is_cancelled(job_id):
            # Cancelled while still queued: it never starts. It must still end
            # terminal — a row left active waits on the stale sweeper, forty minutes
            # away, which reads as a hang. Guarded so an already-cancelled row keeps
            # whatever the API wrote.
            if job.status in ACTIVE_STATUSES:
                set_fields(session, job, status="cancelled", stage="", detail="Cancelled")
            return

        asset = session.get(Asset, job.asset_id)
        if not asset or asset.user_id != job.user_id:
            set_fields(
                session,
                job,
                status="error",
                stage="",
                error_message="The asset was deleted before this could run",
            )
            return

        set_fields(session, job, status="processing", stage="Starting", progress=1, detail="")

        if job.kind == KIND_TRANSCRIBE:
            asset.transcript_status = "running"
            session.add(asset)
            session.commit()

        try:
            if job.kind == KIND_TRANSCRIBE:
                count = run_transcribe(session, asset, progress)
                detail = f"{count} segment{'' if count == 1 else 's'}"
            else:
                raise TranscriptionError(f"Unknown enrichment kind: {job.kind}")

            set_fields(
                session,
                job,
                status="done",
                stage="Done",
                progress=100,
                detail=detail,
                error_message=None,
            )

        except JobCancelled:
            _mark_asset_failed(session, asset, job, status=None)
            # Mark the row here rather than trusting the caller to have done it. The
            # API path marks it before signalling the queue, but the stale sweeper and
            # any direct queue.cancel() do not — and a job left at "processing" sits
            # there until the sweeper ends it forty minutes later, which reads to the
            # user as a hang. Guarded so the API path's "cancelled" is not overwritten.
            if job.status in ACTIVE_STATUSES:
                set_fields(session, job, status="cancelled", stage="", detail="Cancelled")
            logger.info("Enrichment job %s cancelled", job_id)

        except TranscriptionError as exc:
            message = str(exc)
            _mark_asset_failed(session, asset, job)
            set_fields(session, job, status="error", stage="", error_message=message)
            logger.info("Enrichment job %s failed: %s", job_id, message)

        except Exception as exc:  # noqa: BLE001 - a worker must never die silently
            logger.exception("Enrichment job %s crashed", job_id)
            _mark_asset_failed(session, asset, job)
            set_fields(session, job, status="error", stage="", error_message=readable_error(exc))


def _mark_asset_failed(session, asset: Asset, job: EnrichmentJob, status: str | None = "error") -> None:
    """Clear the asset's "running" flag so the UI stops showing a spinner forever."""
    if job.kind != KIND_TRANSCRIBE:
        return
    if asset.transcript_status == "running":
        asset.transcript_status = status
        asset.metadata_modified_date = datetime.utcnow()
        session.add(asset)
        session.commit()
