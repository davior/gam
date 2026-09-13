"""The background job queue.

Tested against a purpose-built job table rather than EnrichmentJob, so these assert the
machinery — cancellation, heartbeats, stale sweeping, restart recovery — without
dragging in ffmpeg or an upstream API. The engine is injected, which is the change from
gecko-notes' version: its tests have to monkeypatch a module global.
"""

import threading
import uuid
from datetime import datetime, timedelta
from typing import Optional

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Field, Session, SQLModel, create_engine

from app.clock import utcnow
from app.jobs.runner import (
    ACTIVE_STATUSES,
    JobCancelled,
    JobQueue,
    is_stale,
    readable_error,
    set_fields,
)


class FakeJob(SQLModel, table=True):
    """A minimal job table with the columns the runner expects."""

    __tablename__ = "fakejob"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(default="u1")
    status: str = Field(default="queued")
    stage: str = Field(default="")
    progress: int = Field(default=0)
    detail: str = Field(default="")
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# Detached from the application's metadata immediately after definition. Declaring a
# table registers it on SQLModel.metadata globally, which had two consequences: every
# test database grew a stray `fakejob` table from create_all, and Alembic autogenerate
# saw a model with no corresponding table and proposed creating one. A test fixture has
# no business appearing in the application's schema.
SQLModel.metadata.remove(FakeJob.__table__)


@pytest.fixture(name="job_engine")
def job_engine_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    # Created directly from the table object, since it is no longer in the metadata.
    FakeJob.__table__.create(engine)
    yield engine
    FakeJob.__table__.drop(engine)


def make_job(engine, **fields) -> str:
    with Session(engine) as session:
        job = FakeJob(**fields)
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


def read_job(engine, job_id: str) -> FakeJob:
    with Session(engine) as session:
        return session.get(FakeJob, job_id)


# ─── running ─────────────────────────────────────────────────────────────────


def test_a_queued_job_runs(job_engine):
    ran = threading.Event()

    def run(job_id: str) -> None:
        with Session(job_engine) as session:
            set_fields(session, session.get(FakeJob, job_id), status="done", progress=100)
        ran.set()

    queue = JobQueue(FakeJob, run, name="test", engine=job_engine)
    queue.start()

    job_id = make_job(job_engine)
    queue.enqueue(job_id)

    assert ran.wait(timeout=5), "the worker never picked the job up"
    assert read_job(job_engine, job_id).status == "done"


def test_a_crashing_job_does_not_kill_the_worker(job_engine):
    """One bad job must not take the queue down with it."""
    first_ran = threading.Event()
    second_ran = threading.Event()

    def run(job_id: str) -> None:
        if not first_ran.is_set():
            first_ran.set()
            raise RuntimeError("boom")
        second_ran.set()

    queue = JobQueue(FakeJob, run, name="test", engine=job_engine)
    queue.start()

    queue.enqueue(make_job(job_engine))
    assert first_ran.wait(timeout=5)

    queue.enqueue(make_job(job_engine))
    assert second_ran.wait(timeout=5), "the worker died with the first job"


# ─── cancellation ────────────────────────────────────────────────────────────


def test_the_reporter_raises_once_cancelled(job_engine):
    """Progress reporting doubles as the cancellation checkpoint, so a job unwinds
    wherever it happens to be reporting rather than polling for it."""
    queue = JobQueue(FakeJob, lambda _job_id: None, name="test", engine=job_engine)
    job_id = make_job(job_engine, status="processing")

    report = queue.reporter(job_id)
    report("Working", 10, "still fine")
    assert read_job(job_engine, job_id).stage == "Working"

    queue.cancel(job_id)
    with pytest.raises(JobCancelled):
        report("Working", 20, "should not be written")

    assert read_job(job_engine, job_id).progress == 10, "a cancelled tick still wrote"


def test_progress_clamps_below_a_hundred(job_engine):
    """Only completion writes 100, so a progress bar cannot sit full while working."""
    queue = JobQueue(FakeJob, lambda _job_id: None, name="test", engine=job_engine)
    job_id = make_job(job_engine)

    report = queue.reporter(job_id)
    report("Working", 150, "")
    assert read_job(job_engine, job_id).progress == 99

    report("Working", -10, "")
    assert read_job(job_engine, job_id).progress == 0


def test_cancellation_is_cleared_after_the_job_finishes(job_engine):
    """Ids are reused across runs in the sense that a stale cancellation flag would
    silently kill a later job."""
    started = threading.Event()

    def run(_job_id: str) -> None:
        started.set()

    queue = JobQueue(FakeJob, run, name="test", engine=job_engine)
    queue.start()

    job_id = make_job(job_engine)
    queue.cancel(job_id)
    queue.enqueue(job_id)

    assert started.wait(timeout=5)
    # Give the finally block a moment to run.
    for _ in range(50):
        if not queue.is_cancelled(job_id):
            break
        threading.Event().wait(0.05)
    assert not queue.is_cancelled(job_id)


# ─── staleness ───────────────────────────────────────────────────────────────


def test_is_stale_only_applies_to_active_jobs():
    long_ago = utcnow() - timedelta(hours=5)

    assert is_stale(FakeJob(status="processing", updated_at=long_ago)) is True
    assert is_stale(FakeJob(status="queued", updated_at=long_ago)) is True
    # A finished job that has not been touched in hours is finished, not stalled.
    assert is_stale(FakeJob(status="done", updated_at=long_ago)) is False
    assert is_stale(FakeJob(status="error", updated_at=long_ago)) is False


def test_is_stale_is_false_for_a_job_that_just_reported():
    assert is_stale(FakeJob(status="processing", updated_at=utcnow())) is False


def test_sweeping_ends_a_job_whose_heartbeat_stopped(job_engine):
    queue = JobQueue(FakeJob, lambda _job_id: None, name="test", engine=job_engine)

    stalled = make_job(job_engine, status="processing")
    with Session(job_engine) as session:
        row = session.get(FakeJob, stalled)
        row.updated_at = utcnow() - timedelta(hours=5)
        session.add(row)
        session.commit()

    healthy = make_job(job_engine, status="processing")

    assert queue.sweep_stale() == 1

    ended = read_job(job_engine, stalled)
    assert ended.status == "error"
    assert "Stopped responding" in ended.error_message
    # Cancelled too, so a thread that later returns from a wedged call unwinds instead
    # of writing for a job already reported as failed.
    assert queue.is_cancelled(stalled)

    assert read_job(job_engine, healthy).status == "processing"


def test_heartbeat_touches_only_the_timestamp(job_engine, monkeypatch):
    """Liveness is not a progress report: it must not overwrite what the job last said
    about itself."""
    from app.config import settings

    monkeypatch.setattr(settings, "job_heartbeat_seconds", 1)

    queue = JobQueue(FakeJob, lambda _job_id: None, name="test", engine=job_engine)
    job_id = make_job(job_engine, status="processing", stage="Transcribing", progress=42)

    with Session(job_engine) as session:
        row = session.get(FakeJob, job_id)
        row.updated_at = utcnow() - timedelta(minutes=10)
        session.add(row)
        session.commit()
    before = read_job(job_engine, job_id).updated_at

    stop = threading.Event()
    thread = threading.Thread(target=queue.heartbeat, args=(job_id, stop), daemon=True)
    thread.start()
    threading.Event().wait(1.5)
    stop.set()
    thread.join(timeout=3)

    after = read_job(job_engine, job_id)
    assert after.updated_at > before
    assert after.stage == "Transcribing"
    assert after.progress == 42


def test_heartbeat_stops_once_the_job_is_no_longer_active(job_engine, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "job_heartbeat_seconds", 1)

    queue = JobQueue(FakeJob, lambda _job_id: None, name="test", engine=job_engine)
    job_id = make_job(job_engine, status="done")

    stop = threading.Event()
    thread = threading.Thread(target=queue.heartbeat, args=(job_id, stop), daemon=True)
    thread.start()
    thread.join(timeout=4)
    assert not thread.is_alive(), "the heartbeat kept beating for a finished job"


# ─── restart recovery ────────────────────────────────────────────────────────


def test_recovery_requeues_work_a_restart_interrupted(job_engine):
    queue = JobQueue(FakeJob, lambda _job_id: None, name="test", engine=job_engine)

    orphaned = make_job(job_engine, status="processing", stage="Transcribing", progress=60)
    finished = make_job(job_engine, status="done", progress=100)
    cancelled = make_job(job_engine, status="cancelled")

    queue.recover_pending()

    requeued = read_job(job_engine, orphaned)
    assert requeued.status == "queued"
    assert requeued.progress == 0
    assert requeued.detail == "Requeued after a restart"

    # A restart must never resurrect a run somebody stopped, or one already finished.
    assert read_job(job_engine, finished).status == "done"
    assert read_job(job_engine, cancelled).status == "cancelled"


# ─── error rendering ─────────────────────────────────────────────────────────


def test_readable_error_pulls_the_message_out_of_an_http_exception():
    from fastapi import HTTPException

    exc = HTTPException(status_code=400, detail={"code": "no_key", "message": "No API key"})
    assert readable_error(exc) == "No API key"


def test_readable_error_falls_back_to_the_exception_text():
    assert readable_error(ValueError("something broke")) == "something broke"


def test_readable_error_never_returns_empty():
    """An empty error message renders as a blank red box, which tells nobody anything."""
    assert readable_error(ValueError()) == "ValueError"


def test_active_statuses_are_what_everything_keys_off():
    assert ACTIVE_STATUSES == ("queued", "processing")
