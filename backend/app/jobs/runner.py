"""The queue and worker threads a background job kind runs on.

Ported from gecko-notes' `app/jobs/runner.py`, whose reasoning holds unchanged and is
worth restating, because it is why this is threads-and-a-queue rather than
FastAPI's BackgroundTasks: transcoding and transcription saturate the CPU or block on a
slow upstream while sharing a container with the API, so unbounded parallelism would
starve request handling; a job needs to be cancellable mid-flight; and a job that was
running when the process restarted has to be picked up rather than left stuck at
"processing" forever.

A kind supplies its table and a `run(job_id)` function. The queue, the worker threads,
cooperative cancellation, heartbeats and restart recovery are all here.

Single process only, deliberately. `_queue` and `_cancelled` are per-process state, so
the concurrency cap and cancellation only hold within one interpreter — which matches
how this is deployed (one uvicorn process over SQLite). Running replicas would mean
moving the queue into the database or a broker, and this file is where that change
would go.

Changed from the original: the engine is injected rather than imported, so a test
constructs a queue against its own database instead of monkeypatching a module global.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Optional, Set, Type

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.config import settings
from app.database import engine as default_engine

logger = logging.getLogger(__name__)

# A job is "active" while it is waiting for, or sitting on, a worker thread. Everything
# derived from "is this still going" keys off this tuple.
ACTIVE_STATUSES = ("queued", "processing")

TERMINAL_STATUSES = ("done", "error", "cancelled")

# How often the sweeper looks for jobs whose heartbeat stopped.
STALE_SWEEP_SECONDS = 60


def stale_after() -> timedelta:
    """How long a job may go silent before it is treated as dead.

    Read at call time rather than captured at import, so a test can shorten it. With a
    heartbeat every 30s this is a backstop rather than the mechanism, and it is set
    well beyond any real run: ending live work is far worse than leaving a dead row
    looking active for a while longer.
    """
    return timedelta(minutes=settings.job_stale_minutes)


def is_stale(job: Any) -> bool:
    """True when an active job has stopped reporting."""
    if getattr(job, "status", None) not in ACTIVE_STATUSES:
        return False
    updated = getattr(job, "updated_at", None)
    if not updated:
        return False
    return datetime.utcnow() - updated > stale_after()


def set_fields(session: Session, job: Any, **fields: Any) -> None:
    """Write fields to a job row and commit, touching `updated_at`.

    `updated_at` advancing on every write is what makes it a heartbeat: a row that
    stops moving is a worker that stopped working.
    """
    for key, value in fields.items():
        setattr(job, key, value)
    job.updated_at = datetime.utcnow()
    session.add(job)
    session.commit()


def readable_error(exc: Exception) -> str:
    """An exception as something worth showing a user.

    Callers raise HTTPException, whose str() is the raw `400: {'code': ...}` repr. The
    message inside it is the part someone can act on — "Deepgram API key is not
    configured" rather than a traceback.
    """
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict) and detail.get("message"):
        return str(detail["message"])
    if isinstance(detail, str) and detail:
        return detail
    return str(exc) or exc.__class__.__name__


class JobCancelled(Exception):
    """Raised at a checkpoint to unwind a job somebody stopped."""


class JobQueue:
    """One queue, its worker threads, and the cancellation set for a job table."""

    def __init__(
        self,
        model: Type[Any],
        run: Callable[[str], None],
        *,
        name: str,
        concurrency: int = 1,
        engine: Optional[Engine] = None,
    ) -> None:
        self.model = model
        self.run = run
        self.name = name
        self.concurrency = max(1, concurrency)
        self.engine = engine or default_engine
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._cancelled: Set[str] = set()
        self._lock = threading.Lock()
        self._started = False

    def session(self) -> Session:
        return Session(self.engine)

    # ─── control ─────────────────────────────────────────────────────────────

    def enqueue(self, job_id: str) -> None:
        self._queue.put(job_id)

    def cancel(self, job_id: str) -> None:
        """Ask a job to stop.

        A queued job never starts; a running one unwinds at its next checkpoint,
        wherever the kind chose to put those.
        """
        with self._lock:
            self._cancelled.add(job_id)

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelled

    # ─── lifecycle ───────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the workers and requeue anything a restart interrupted.

        Idempotent: the app lifespan is not the only caller, and a test may start a
        queue directly.
        """
        if self._started:
            return
        self._started = True

        for index in range(self.concurrency):
            threading.Thread(
                target=self._loop, daemon=True, name=f"{self.name}-{index}"
            ).start()
        threading.Thread(
            target=self._sweeper, daemon=True, name=f"{self.name}-sweep"
        ).start()
        self.recover_pending()

    def sweep_stale(self) -> int:
        """End jobs whose heartbeat stopped, and tell their worker to unwind.

        Cancelling as well as marking matters: a thread that later returns from a
        wedged call must not go on writing for a job already reported as failed, and
        the cancelled set is what its next checkpoint reads.
        """
        swept = 0
        try:
            with self.session() as session:
                rows = session.exec(
                    select(self.model).where(self.model.status.in_(ACTIVE_STATUSES))
                ).all()
                for row in rows:
                    if not is_stale(row):
                        continue
                    self.cancel(row.id)
                    fields: dict = {
                        "status": "error",
                        "error_message": "Stopped responding and was ended automatically",
                    }
                    if hasattr(row, "stage"):
                        fields["stage"] = ""
                    set_fields(session, row, **fields)
                    swept += 1
                if swept:
                    logger.warning("Ended %d stalled %s job(s)", swept, self.name)
        except Exception:
            logger.exception("Could not sweep stalled %s jobs", self.name)
        return swept

    def _sweeper(self) -> None:
        while True:
            time.sleep(STALE_SWEEP_SECONDS)
            self.sweep_stale()

    def heartbeat(self, job_id: str, stop: threading.Event) -> None:
        """Say "still alive" until `stop` is set.

        Only `updated_at` is touched, never stage or progress: this is liveness, not a
        progress report, and it must not overwrite what the job last said about itself.
        It returns early once the row is no longer active, so a finished or cancelled
        job is not kept looking live.
        """
        interval = max(1, settings.job_heartbeat_seconds)
        while not stop.wait(interval):
            try:
                with self.session() as session:
                    row = session.get(self.model, job_id)
                    if not row or row.status not in ACTIVE_STATUSES:
                        return
                    row.updated_at = datetime.utcnow()
                    session.add(row)
                    session.commit()
            except Exception:
                # A missed beat is survivable; a heartbeat that kills the worker is not.
                logger.exception("Heartbeat failed for %s job %s", self.name, job_id)

    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            # Beats for as long as the job runs, so genuinely slow work is not mistaken
            # for a dead worker. Without it the sweeper ends a long transcription
            # mid-flight — and it does not merely release it, it marks it failed.
            stop = threading.Event()
            threading.Thread(
                target=self.heartbeat,
                args=(job_id, stop),
                daemon=True,
                name=f"{self.name}-beat",
            ).start()
            try:
                self.run(job_id)
            except Exception:
                logger.exception("Unhandled error in the %s worker", self.name)
            finally:
                stop.set()
                with self._lock:
                    self._cancelled.discard(job_id)
                self._queue.task_done()

    def recover_pending(self) -> None:
        """Re-enqueue work that outlived the process doing it.

        A job left at "processing" by a restart has no worker any more, so it goes back
        on the queue and starts over. Finished and cancelled rows are untouched, so a
        restart never resurrects a run somebody stopped.
        """
        try:
            with self.session() as session:
                rows = session.exec(
                    select(self.model).where(self.model.status.in_(ACTIVE_STATUSES))
                ).all()
                for row in rows:
                    fields: dict = {"status": "queued"}
                    if hasattr(row, "stage"):
                        fields["stage"] = ""
                    if hasattr(row, "progress"):
                        fields["progress"] = 0
                    if hasattr(row, "detail"):
                        fields["detail"] = "Requeued after a restart"
                    set_fields(session, row, **fields)
                    self._queue.put(row.id)
                if rows:
                    logger.info("Requeued %d unfinished %s job(s)", len(rows), self.name)
        except Exception:
            logger.exception("Could not recover pending %s jobs", self.name)

    # ─── progress ────────────────────────────────────────────────────────────

    def reporter(self, job_id: str) -> Callable[[str, int, str], None]:
        """The `(stage, percent, detail)` callback a long job reports through.

        It doubles as the cancellation checkpoint: it raises JobCancelled if the job
        has been stopped, so a job unwinds wherever it happens to be reporting rather
        than needing its own polling. Percent clamps to 99 so only completion writes
        100, and each tick opens its own session because this runs on a worker thread,
        not a request.
        """

        def report(stage: str, percent: int, detail: str = "") -> None:
            if self.is_cancelled(job_id):
                raise JobCancelled(job_id)
            with self.session() as session:
                row = session.get(self.model, job_id)
                if row:
                    set_fields(
                        session,
                        row,
                        stage=stage,
                        progress=max(0, min(99, percent)),
                        detail=detail,
                    )

        return report
