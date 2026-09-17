"""The enrichment worker: one queue, dispatching on a job's kind.

One queue rather than one per action, because they contend for the same things — CPU
for ffmpeg, and a rate-limited upstream — and separate queues would each honour their
own cap while collectively ignoring it.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session, col, select

from app.clock import utcnow
from app.config import settings
from app.database import engine
from app.embeddings import EmbeddingError, build_embedder
from app.enrichment.embed import EmbeddingUnavailable
from app.enrichment.backfill import run as run_backfill
from app.enrichment.embed import run as run_embed
from app.enrichment.autotag import run as run_autotag
from app.enrichment.bulk import run as run_bulk
from app.enrichment.describe import run as run_describe
from app.enrichment.extract_text import TextExtractionError
from app.enrichment.extract_text import run as run_extract_text
from app.enrichment.generate_all import run as run_generate_all
from app.enrichment.source import NoSourceMaterial
from app.enrichment.summarize import run as run_summarize
from app.enrichment.transcribe import TranscriptionError
from app.enrichment.transcribe import run as run_transcribe
from app.providers.base import ProviderError
from app.jobs.runner import (
    ACTIVE_STATUSES,
    JobCancelled,
    JobQueue,
    readable_error,
    set_fields,
)
from app.models.asset import Asset
from app.models.job import (
    EnrichmentJob,
    KIND_AUTOTAG,
    KIND_BACKFILL_EMBEDDINGS,
    KIND_BULK_ENRICH,
    KIND_DESCRIBE,
    KIND_EMBED,
    KIND_EXTRACT_TEXT,
    KIND_GENERATE_ALL,
    KIND_SUMMARIZE,
    KIND_TRANSCRIBE,
    LIBRARY_KINDS,
)

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


def active_job(session: Session, asset_id: str, kind: str) -> Optional[EnrichmentJob]:
    """A queued or running job of this kind for this asset, if there is one.

    One at a time per asset per kind: two concurrent runs race to replace the same rows
    and bill twice for the same work.
    """
    return session.exec(
        select(EnrichmentJob).where(
            EnrichmentJob.asset_id == asset_id,
            EnrichmentJob.kind == kind,
            col(EnrichmentJob.status).in_(ACTIVE_STATUSES),
        )
    ).first()


def active_library_job(session: Session, user_id: str, kind: str) -> Optional[EnrichmentJob]:
    """A queued or running whole-library job of this kind, if there is one.

    Keyed on the user rather than an asset, because that is what a library-wide job is
    scoped to. Two concurrent backfills would embed the same assets twice and bill for
    it.
    """
    return session.exec(
        select(EnrichmentJob).where(
            EnrichmentJob.user_id == user_id,
            EnrichmentJob.kind == kind,
            col(EnrichmentJob.status).in_(ACTIVE_STATUSES),
        )
    ).first()


def submit_library(
    session: Session,
    user_id: str,
    kind: str,
    *,
    model: str = "",
    payload: Optional[str] = None,
) -> EnrichmentJob:
    """Queue a job that is about the whole library rather than one asset.

    `asset_id` stays null and `asset_name` empty; the activity row reads "Your whole
    library" on the client side. `model` is recorded at submit time so the row says
    which model it is filling, the same reason a transcript records the model that
    produced it.
    """
    job = EnrichmentJob(
        user_id=user_id,
        asset_id=None,
        kind=kind,
        status="queued",
        stage="Queued",
        asset_name="",
        model=model,
        payload=payload,
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    enqueue(job.id)
    return job


def submit(session: Session, asset: Asset, kind: str) -> EnrichmentJob:
    """Create a job row and hand it to the queue.

    The row is committed before the queue is told about it. The worker looks the job up
    by id in its own session, so enqueueing first is a race it can lose.
    """
    job = EnrichmentJob(
        user_id=asset.user_id,
        asset_id=asset.id,
        kind=kind,
        status="queued",
        stage="Queued",
        asset_name=asset.name,
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    enqueue(job.id)
    return job


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

        # A whole-library job has no asset to look up, and demanding one would make
        # every backfill fail here. Branching on the set rather than a hardcoded kind
        # means another library-wide action later needs no change to this block.
        asset: Optional[Asset] = None
        if job.kind not in LIBRARY_KINDS:
            asset = session.get(Asset, job.asset_id) if job.asset_id else None
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
            elif job.kind == KIND_EMBED:
                count = run_embed(session, asset, progress)
                detail = f"{count} vector{'' if count == 1 else 's'}"
            elif job.kind == KIND_EXTRACT_TEXT:
                detail = run_extract_text(session, asset, progress)
            elif job.kind == KIND_SUMMARIZE:
                detail = run_summarize(session, asset, progress)
            elif job.kind == KIND_AUTOTAG:
                detail = run_autotag(session, asset, progress)
            elif job.kind == KIND_DESCRIBE:
                detail = run_describe(session, asset, progress)
            elif job.kind == KIND_GENERATE_ALL:
                detail = run_generate_all(session, asset, progress)
            elif job.kind == KIND_BULK_ENRICH:
                bulk = run_bulk(session, job.user_id, job.payload, progress)
                detail = f"{bulk.done} done"
                if bulk.failed:
                    # Surfaced rather than swallowed: a run that quietly skipped three
                    # assets looks identical to one that finished them all.
                    detail += f", {bulk.failed} failed"
                if bulk.skipped:
                    detail += f", {bulk.skipped} no longer there"
            elif job.kind == KIND_BACKFILL_EMBEDDINGS:
                result = run_backfill(session, job.user_id, progress)
                detail = f"{result.embedded} embedded"
                if result.failed:
                    # Surfaced rather than swallowed: a run that quietly skipped three
                    # assets looks identical to one that embedded everything.
                    detail += f", {result.failed} failed"
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

            if job.kind == KIND_TRANSCRIBE:
                # Not KIND_EXTRACT_TEXT: `embed` vectorises an asset's name, description
                # and summary plus its transcript segments, and document pages are in
                # none of those. Chaining it here would queue a job that does no new
                # work and put a row in the activity feed saying so. It belongs here the
                # day page bodies are embedded — see the search gap in plan-of-attack.
                _chain_embedding(session, asset)

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

        except ValueError as exc:
            message = str(exc)
            _mark_asset_failed(session, asset, job)
            set_fields(session, job, status="error", stage="", error_message=message)
            logger.info("Enrichment job %s could not run: %s", job_id, message)

        except (ProviderError, NoSourceMaterial) as exc:
            message = str(exc)
            _mark_asset_failed(session, asset, job)
            set_fields(session, job, status="error", stage="", error_message=message)
            logger.info("Enrichment job %s failed: %s", job_id, message)

        except (EmbeddingUnavailable, EmbeddingError) as exc:
            message = str(exc)
            _mark_asset_failed(session, asset, job)
            set_fields(session, job, status="error", stage="", error_message=message)
            logger.info("Enrichment job %s failed: %s", job_id, message)

        except TextExtractionError as exc:
            message = str(exc)
            _mark_asset_failed(session, asset, job)
            set_fields(session, job, status="error", stage="", error_message=message)
            logger.info("Enrichment job %s failed: %s", job_id, message)

        except TranscriptionError as exc:
            message = str(exc)
            _mark_asset_failed(session, asset, job)
            set_fields(session, job, status="error", stage="", error_message=message)
            logger.info("Enrichment job %s failed: %s", job_id, message)

        except Exception as exc:  # noqa: BLE001 - a worker must never die silently
            logger.exception("Enrichment job %s crashed", job_id)
            _mark_asset_failed(session, asset, job)
            set_fields(session, job, status="error", stage="", error_message=readable_error(exc))


def _chain_embedding(session: Session, asset: Asset) -> None:
    """Embed an asset as soon as it has a transcript, without being asked.

    A transcript that is not embedded is invisible to half of search, and nothing else
    in the app would ever ask for the embed job — asking the user to press a second
    button to make the first one count is not a feature.

    Guarded on a provider actually being configured. Queueing unconditionally would put
    a red "no embedding provider" row in the activity feed after every single
    transcription, which trains people to ignore the feed.
    """
    try:
        if build_embedder(session, asset.user_id) is None:
            logger.info(
                "Not embedding asset %s: no embedding provider configured for this user",
                asset.id,
            )
            return

        if active_job(session, asset.id, KIND_EMBED) is not None:
            return

        submit(session, asset, KIND_EMBED)
    except Exception:  # noqa: BLE001 - a transcript that succeeded must stay succeeded
        # The whole body, not just the submit: reading the provider config parses stored
        # values, and a bad one must not turn a finished transcription into a failed job.
        logger.warning("Could not queue embedding for asset %s", asset.id, exc_info=True)


def _mark_asset_failed(session, asset: Optional[Asset], job: EnrichmentJob, status: str | None = "error") -> None:
    """Clear the asset's "running" flag so the UI stops showing a spinner forever."""
    # A whole-library job carries no asset, and the callers now pass None.
    if asset is None:
        return
    if job.kind != KIND_TRANSCRIBE:
        return
    if asset.transcript_status == "running":
        asset.transcript_status = status
        asset.metadata_modified_date = utcnow()
        session.add(asset)
        session.commit()
