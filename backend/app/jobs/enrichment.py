"""The enrichment worker: one queue, dispatching on a job's kind.

One queue rather than one per action, because they contend for the same things — CPU
for ffmpeg, and a rate-limited upstream — and separate queues would each honour their
own cap while collectively ignoring it.

The exception is generation (M8), which gets a second queue over the same table. It
contends for none of those things: a generation is minutes of waiting on fal.ai with
the CPU idle, and sharing the one worker would leave every transcription queued behind
a video. Both queues run the same `_run`; the kind filter on each is what stops them
recovering or sweeping each other's rows.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from sqlmodel import Session, col, select

from app.clock import utcnow
from app.config import settings
from app.database import engine
from app.embeddings import EmbeddingError, build_embedder
from app.enrichment.embed import EmbeddingUnavailable
from app.enrichment.attribute import run as run_attribute
from app.enrichment.backfill import run as run_backfill
from app.enrichment.harvest_attribution import run as run_harvest_attribution
from app.enrichment.embed import run as run_embed
from app.enrichment.autotag import run as run_autotag
from app.enrichment.bulk import run as run_bulk
from app.enrichment.describe import run as run_describe
from app.enrichment.extract_subvideo import run as run_extract_subvideo
from app.enrichment.extract_text import TextExtractionError
from app.enrichment.extract_text import run as run_extract_text
from app.enrichment.generate_all import run as run_generate_all
from app.enrichment.import_url import run as run_import_url
from app.enrichment.source import NoSourceMaterial
from app.enrichment.subvideo import SubvideoExtractionError
from app.enrichment.summarize import run as run_summarize
from app.enrichment.transcribe import TranscriptionError
from app.enrichment.transcribe import run as run_transcribe
from app.generation.errors import GenerationError
from app.generation.run import run as run_generate
from app.ingest.ytdlp import UrlImportError
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
    KIND_ATTRIBUTE,
    KIND_BACKFILL_EMBEDDINGS,
    KIND_HARVEST_ATTRIBUTION,
    KIND_BULK_ENRICH,
    KIND_DESCRIBE,
    KIND_EMBED,
    KIND_EXTRACT_SUBVIDEO,
    KIND_EXTRACT_TEXT,
    GENERATION_JOB_KINDS,
    KIND_GENERATE,
    KIND_GENERATE_ALL,
    KIND_IMPORT_URL,
    KIND_SUMMARIZE,
    KIND_TRANSCRIBE,
    LIBRARY_KINDS,
)

logger = logging.getLogger(__name__)

_queue: Optional[JobQueue] = None
_generation_queue: Optional[JobQueue] = None


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
            exclude_kinds=GENERATION_JOB_KINDS,
        )
    return _queue


def generation_queue() -> JobQueue:
    """The process's generation queue, built on first use, for the same reason."""
    global _generation_queue
    if _generation_queue is None:
        _generation_queue = JobQueue(
            EnrichmentJob,
            _run_generation_job,
            name="generation",
            concurrency=settings.generation_concurrency,
            engine=engine,
            kinds=GENERATION_JOB_KINDS,
        )
    return _generation_queue


def queue_for(kind: str) -> JobQueue:
    """Whichever queue runs this kind — and therefore the one that can cancel it."""
    return generation_queue() if kind in GENERATION_JOB_KINDS else queue()


def start() -> None:
    # Both from here, so the one `start` the app lifespan calls — and the one conftest
    # stubs out — governs every worker thread there is.
    queue().start()
    generation_queue().start()


def enqueue(job_id: str, kind: Optional[str] = None) -> None:
    (queue_for(kind) if kind else queue()).enqueue(job_id)


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
    asset_name: str = "",
) -> EnrichmentJob:
    """Queue a job that is not about one existing asset.

    `asset_id` stays null. `asset_name` is usually empty too, and the activity row then
    reads "Your whole library" on the client side — right for a backfill, wrong for a URL
    import, which is about one thing that simply does not exist yet. That caller passes
    the URL, and the job replaces it with the title once it knows one. `model` is
    recorded at submit time so the row says which model it is filling, the same reason a
    transcript records the model that produced it.
    """
    job = EnrichmentJob(
        user_id=user_id,
        asset_id=None,
        kind=kind,
        status="queued",
        stage="Queued",
        asset_name=asset_name,
        model=model,
        payload=payload,
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    enqueue(job.id, job.kind)
    return job


def submit(
    session: Session, asset: Asset, kind: str, *, payload: Optional[str] = None
) -> EnrichmentJob:
    """Create a job row and hand it to the queue.

    The row is committed before the queue is told about it. The worker looks the job up
    by id in its own session, so enqueueing first is a race it can lose.

    `payload` is optional and defaults to `None` for every existing caller — only
    `KIND_EXTRACT_SUBVIDEO` needs it today, to carry in/out points and mode in rather
    than inventing a second submit function for the one kind that needs more than an
    asset. `submit_library` already takes one for the same reason, one level up.
    """
    job = EnrichmentJob(
        user_id=asset.user_id,
        asset_id=asset.id,
        kind=kind,
        status="queued",
        stage="Queued",
        asset_name=asset.name,
        payload=payload,
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    enqueue(job.id, job.kind)
    return job


def _run_job(job_id: str) -> None:
    """The enrichment queue's worker function."""
    _run(job_id, queue())


def _run_generation_job(job_id: str) -> None:
    """The generation queue's. Same body; the queue is what differs — its reporter is
    the one whose cancel set the API writes to for this kind."""
    _run(job_id, generation_queue())


def _run(job_id: str, q: JobQueue) -> None:
    """Run one job to completion, recording whatever happened.

    Every exit writes a terminal status. A job that ends without one sits at
    "processing" until the stale sweeper ends it forty minutes later, which reads to
    the user as a hang.
    """
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

        # An import that stored the site's captions as a transcript, to embed afterwards
        # the same way a finished transcription is.
        captioned: Optional[Asset] = None
        # What a generation made, embedded afterwards for the same reason: its prompt
        # is its description, and semantic search reads that too.
        generated: list[Asset] = []

        try:
            if job.kind == KIND_TRANSCRIBE:
                count = run_transcribe(session, asset, progress)
                detail = f"{count} segment{'' if count == 1 else 's'}"
            elif job.kind == KIND_EMBED:
                count = run_embed(session, asset, progress)
                detail = f"{count} vector{'' if count == 1 else 's'}"
            elif job.kind == KIND_EXTRACT_TEXT:
                detail = run_extract_text(session, asset, progress)
            elif job.kind == KIND_EXTRACT_SUBVIDEO:
                detail, created_asset_id = run_extract_subvideo(session, asset, job.payload, progress)
                # Only "extract" mode returns one — "promote" mutates `asset` (the
                # clip) in place, and the frontend already has that id as `job.asset_id`
                # throughout, so there is nothing new to report. Set directly on the
                # live `job` row rather than threaded through `set_fields` below: that
                # call's kwargs are the terminal-status fields, and `payload` is not
                # one of them, so this survives it untouched.
                if created_asset_id:
                    job.payload = json.dumps({"created_asset_id": created_asset_id})
            elif job.kind == KIND_SUMMARIZE:
                detail = run_summarize(session, asset, progress)
            elif job.kind == KIND_AUTOTAG:
                detail = run_autotag(session, asset, progress)
            elif job.kind == KIND_ATTRIBUTE:
                detail = run_attribute(session, asset, progress)
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
            elif job.kind == KIND_IMPORT_URL:
                imported = run_import_url(session, job, progress)
                detail = imported.detail
                captioned = imported.captioned_asset
                if imported.created_asset_id:
                    # Merged into the request rather than replacing it, unlike the
                    # sub-video branch above: the URL and options stay readable on a
                    # finished row, which is the first thing to look at when an import
                    # produced something unexpected.
                    job.payload = _with_created_asset(job.payload, imported.created_asset_id)
            elif job.kind == KIND_GENERATE:
                # The created ids are already in the payload — the job writes each as it
                # is made, so a restart mid-way knows what not to make again.
                outcome = run_generate(session, job, progress)
                detail = outcome.detail
                generated = outcome.created_assets
            elif job.kind == KIND_HARVEST_ATTRIBUTION:
                harvested = run_harvest_attribution(session, job.user_id, progress)
                detail = (
                    f"{harvested.attributed} attributed of {harvested.scanned} scanned"
                )
                if harvested.failed:
                    # Surfaced rather than swallowed, same as the two runs above.
                    detail += f", {harvested.failed} unreadable"
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
            elif captioned is not None:
                _chain_embedding(session, captioned)
            for asset_made in generated:
                _chain_embedding(session, asset_made)

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

        except SubvideoExtractionError as exc:
            message = str(exc)
            _mark_asset_failed(session, asset, job)
            set_fields(session, job, status="error", stage="", error_message=message)
            logger.info("Enrichment job %s failed: %s", job_id, message)

        except UrlImportError as exc:
            message = str(exc)
            set_fields(session, job, status="error", stage="", error_message=message)
            logger.info("Enrichment job %s failed: %s", job_id, message)

        except GenerationError as exc:
            # A rejected key, a refused prompt, a timeout: an answer from fal or from the
            # job's own limits, already worded for the activity feed. No traceback.
            message = str(exc)
            set_fields(session, job, status="error", stage="", error_message=message)
            logger.info("Generation job %s failed: %s", job_id, message)

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


def _with_created_asset(payload: Optional[str], asset_id: str) -> str:
    """The job's payload with the asset it produced added, for `registry._result_asset_id`."""
    try:
        data = json.loads(payload or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["created_asset_id"] = asset_id
    return json.dumps(data, sort_keys=True)


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
