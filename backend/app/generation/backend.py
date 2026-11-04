"""Where a generation's fal.ai requests go: straight to fal, or through Gecko Suite.

`run.py` needs four things of fal (submit, status, result, cancel) and one of the bill.
This is the seam between that and where they happen, so the job itself, with its
resumption, its downloads and its provenance, is the same in both modes.

**Local** is what GAM always did: the person's own fal key from Settings, fal's queue
spoken to directly, and the cost worked out and written to this app's own usage table.

**Suite** (`AI_BACKEND=suite`) sends the same request body to the suite, which holds the
key, runs the queue and records the cost under GAM and the asset. Three consequences:
- The handle a job persists is the suite's job id, not fal's URLs, so a restart resumes
  by id alone and this app never holds an address the key is sent to.
- GAM writes no usage row for it (`records_usage`): the suite has, and a second row would
  count the generation twice wherever both are read.
- The model catalogue, each endpoint's request format, stays here. The suite is a gateway
  for the key, the queue and the bill, not (yet) for what to ask a model for.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from gecko_suite_client import MediaJob, SuiteError, SuiteMedia, SuiteUnavailable

from app import suite
from app.generation import fal
from app.generation.errors import FalUnavailable, GenerationError
from app.models.generation import KIND_IMAGE_TO_VIDEO
from app.settings_store import load_fal_key
from sqlmodel import Session


class Backend(Protocol):
    # True when whoever ran the request has already written its cost somewhere, and GAM
    # must not write it again.
    records_usage: bool

    def submit(self, endpoint_id: str, body: dict, *, kind: str) -> Any: ...
    def status(self, handle: Any) -> fal.QueueStatus: ...
    def result(self, handle: Any) -> fal.QueueResult: ...
    def cancel(self, handle: Any) -> bool: ...


class LocalBackend:
    records_usage = False

    def __init__(self, api_key: str):
        self.api_key = api_key

    def submit(self, endpoint_id: str, body: dict, *, kind: str) -> fal.QueuedRequest:
        return fal.submit(endpoint_id, body, api_key=self.api_key)

    def status(self, handle: fal.QueuedRequest) -> fal.QueueStatus:
        return fal.status(handle, api_key=self.api_key)

    def result(self, handle: fal.QueuedRequest) -> fal.QueueResult:
        return fal.result(handle, api_key=self.api_key)

    def cancel(self, handle: fal.QueuedRequest) -> bool:
        return fal.cancel(handle, api_key=self.api_key)


@dataclass(frozen=True)
class SuiteHandle:
    """What a suite-run job persists in place of fal's queue URLs: the suite's job id.
    Named `request_id` so the job's logs and usage reference read the same either way."""

    request_id: str

    def to_dict(self) -> dict:
        return {"suite_job_id": self.request_id}

    @classmethod
    def from_dict(cls, data: Any) -> Optional["SuiteHandle"]:
        if isinstance(data, dict) and isinstance(data.get("suite_job_id"), str):
            return cls(data["suite_job_id"])
        return None


def handle_from_dict(data: Any):
    """A persisted handle, whichever mode wrote it. A job started under one mode and
    resumed under the other (the switch is a restart) is followed by the mode that
    started it, if it can be: a suite job id means the suite."""
    return SuiteHandle.from_dict(data) or fal.QueuedRequest.from_dict(data)


class SuiteBackend:
    records_usage = True

    def __init__(self, user_id: str):
        self.user_id = user_id
        # The last state the suite reported per job. The suite fetches the result on the
        # poll that finds fal finished, so `result` is that poll's answer, not a new call.
        self._seen: dict[str, MediaJob] = {}

    def _call(self, action):
        try:
            with closing(suite.client(self.user_id)) as connection:
                return action(SuiteMedia(connection, app=suite.APP))
        except SuiteUnavailable as exc:
            # Transient, like fal's own 429/5xx: a video four minutes in must not be
            # abandoned, and billed for nothing, over the suite restarting.
            raise FalUnavailable("Gecko Suite can't be reached right now") from exc
        except SuiteError as exc:
            raise GenerationError(exc.message) from exc

    def submit(self, endpoint_id: str, body: dict, *, kind: str) -> SuiteHandle:
        job = self._call(lambda media: media.submit(
            "video" if kind == KIND_IMAGE_TO_VIDEO else "image", endpoint_id, body))
        self._seen[job.id] = job
        return SuiteHandle(job.id)

    def status(self, handle: SuiteHandle) -> fal.QueueStatus:
        job = self._call(lambda media: media.get(handle.request_id))
        self._seen[job.id] = job
        if job.status == "failed":
            # fal reports a failed run as COMPLETED with the reason beside it, and the job
            # already reads that shape.
            return fal.QueueStatus(fal.STATUS_COMPLETED, error=job.error or "it did not finish")
        if job.status == "cancelled":
            return fal.QueueStatus(fal.STATUS_COMPLETED, error="the request was cancelled")
        state = {"queued": fal.STATUS_IN_QUEUE, "running": fal.STATUS_IN_PROGRESS,
                 "completed": fal.STATUS_COMPLETED}.get(job.status, fal.STATUS_IN_PROGRESS)
        return fal.QueueStatus(state, queue_position=job.queue_position)

    def result(self, handle: SuiteHandle) -> fal.QueueResult:
        job = self._seen.get(handle.request_id)
        if job is None or job.status != "completed":
            job = self._call(lambda media: media.get(handle.request_id))
        if job.status != "completed" or not isinstance(job.result, dict):
            raise GenerationError("Gecko Suite has no result for this request yet")
        return fal.QueueResult(body=job.result, billable_units=job.billable_units,
                               request_id=handle.request_id)

    def cancel(self, handle: SuiteHandle) -> bool:
        try:
            self._call(lambda media: media.cancel(handle.request_id))
        except GenerationError:
            return False
        return True


def backend_for(session: Session, user_id: str) -> Backend:
    """The backend for this person's generation, or a GenerationError saying what is
    missing."""
    if suite.enabled():
        return SuiteBackend(user_id)
    api_key = load_fal_key(session, user_id)
    if not api_key:
        raise GenerationError("No fal.ai API key is configured. Add one in Settings.")
    return LocalBackend(api_key)
