"""Generation through Gecko Suite (`AI_BACKEND=suite`), with the suite faked.

The job is the same job: the same request body from the catalogue, the same downloads,
saves and provenance, the same resumption. What changes is where fal's queue is spoken
to (the suite, as GAM for this person), what is persisted to follow it (the suite's job
id, never fal's URLs) and who writes the bill (the suite, so GAM writes none).
"""

import json
import shutil
from typing import List

import httpx
import pytest
from sqlmodel import select

from app import suite
from app.config import settings
from app.generation import backend as fal_backend
from app.generation import fal
from app.generation import run as run_module
from app.models.asset import Asset
from app.models.job import EnrichmentJob
from app.models.usage import UsageEvent
from tests.fal_stub import FIXTURES, T2I, TEST_USER, run_job, seed_models, set_fal_key
from tests.test_suite_backend import SECRET

RESULT = {"images": [{"url": "https://v3.fal.media/files/out-0.png", "content_type": "image/png",
                      "width": 1024, "height": 1024}], "seed": 7}


def job(status="completed", **fields) -> dict:
    return {"id": "sj-1", "kind": "image", "endpoint_id": T2I, "status": status,
            "queue_position": None, "error": None, "result": None, "billable_units": None,
            "cost": None, "currency": None, "cost_estimated": None, "usage_event_id": None,
            "provider": "fal.ai", **fields}


DONE = job(result=RESULT, billable_units="1", cost=0.025, currency="USD", cost_estimated=False)


class FakeSuiteMedia:
    def __init__(self):
        self.requests: List[httpx.Request] = []
        self.submitted: List[dict] = []
        self.polls: list = [DONE]  # answered in order; the last repeats
        self.submit_response = None
        self.cancels = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/ai/media/jobs":
            if self.submit_response is not None:
                return self.submit_response
            self.submitted.append(json.loads(request.content))
            return httpx.Response(202, json={"data": job("queued")})
        if request.method == "GET" and path == "/api/v1/ai/media/jobs/sj-1":
            answer = self.polls[0] if len(self.polls) == 1 else self.polls.pop(0)
            if isinstance(answer, type) and issubclass(answer, Exception):
                raise answer("refused")
            if isinstance(answer, httpx.Response):
                return answer
            return httpx.Response(200, json={"data": answer})
        if request.method == "DELETE" and path == "/api/v1/ai/media/jobs/sj-1":
            self.cancels += 1
            return httpx.Response(200, json={"data": job("cancelled")})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


@pytest.fixture(name="media")
def media_fixture(monkeypatch, session):
    fake = FakeSuiteMedia()
    monkeypatch.setattr(settings, "ai_backend", "suite")
    monkeypatch.setattr(settings, "suite_app_secret", SECRET)
    monkeypatch.setattr(suite, "TRANSPORT", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(run_module, "sleep", lambda seconds: None)

    def download(url, workdir, stem, **kwargs):
        # The CDN is fal's, not the suite's, and is faked by the download test elsewhere.
        target = workdir / f"{stem}.png"
        shutil.copy(FIXTURES / "sample_image.png", target)
        return target

    monkeypatch.setattr(run_module, "download", download)
    return fake


@pytest.fixture
def models(session):
    return seed_models(session)


def _start(library, models):
    res = library.post("/api/generate", json={
        "model_id": models[T2I].id, "prompt": "A red fox in snow",
        "params": {"image_size": "square_hd", "num_outputs": 1}})
    assert res.status_code == 202, res.text
    return res.json()["data"]["id"]


def _generated(session):
    session.expire_all()
    return session.exec(select(Asset).where(Asset.source == "ai_generated")).all()


def _row(session, job_id):
    session.expire_all()
    return session.get(EnrichmentJob, job_id)


def test_a_generation_runs_on_the_suite_and_gam_writes_no_bill(
        library, session, monkeypatch, media, models):
    # No fal key in GAM's Settings: the key is the suite's.
    job_id = _start(library, models)
    row = run_job(session, monkeypatch, job_id)

    assert row.status == "done", row.error_message
    (asset,) = _generated(session)
    assert asset.ai_model == T2I and asset.source == "ai_generated"

    # The suite was sent the catalogue's body with the prompt, as GAM for this person.
    assert media.submitted == [{"kind": "image", "endpoint_id": T2I, "app": "gam", "input": {
        "image_size": "square_hd", "num_images": 1, "prompt": "A red fox in snow"}}]
    sent = media.requests[0].headers
    assert sent["x-gecko-app"] == "gam" and sent["x-on-behalf-of"] == TEST_USER
    assert "authorization" not in sent

    # What is kept to follow the request is the suite's job id, never an address.
    payload = json.loads(row.payload)
    assert payload["fal_request"] == {"suite_job_id": "sj-1"}
    # The suite recorded the cost. A row here too would count the generation twice.
    assert session.exec(select(UsageEvent)).all() == []


def test_a_job_waits_through_the_queue_and_survives_the_suite_blinking(
        library, session, monkeypatch, media, models):
    media.polls = [job("queued", queue_position=1), job("running"), httpx.ConnectError,
                   job("running"), DONE]
    row = run_job(session, monkeypatch, _start(library, models))
    # A suite that is briefly down says nothing about a generation that is still running
    # and still billed: it is asked again, not abandoned.
    assert row.status == "done", row.error_message
    assert len(_generated(session)) == 1
    assert media.cancels == 0


def test_a_failed_run_ends_the_job_with_the_reason_and_saves_nothing(
        library, session, monkeypatch, media, models):
    media.polls = [job("failed", error="The prompt was refused")]
    row = run_job(session, monkeypatch, _start(library, models))
    assert row.status == "error" and "The prompt was refused" in row.error_message
    assert _generated(session) == []


def test_the_suites_own_sentence_reaches_the_activity_feed(
        library, session, monkeypatch, media, models):
    media.submit_response = httpx.Response(409, json={"detail": {
        "code": "ai_not_configured",
        "message": "No fal.ai key is set up. Add a fal.ai provider in Gecko Suite."}})
    row = run_job(session, monkeypatch, _start(library, models))
    assert row.status == "error" and "Add a fal.ai provider in Gecko Suite" in row.error_message


def test_a_suite_that_is_down_at_submit_fails_the_job_without_a_charge(
        library, session, monkeypatch, media, models):
    media.submit_response = None
    monkeypatch.setattr(suite, "TRANSPORT", httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused"))))
    row = run_job(session, monkeypatch, _start(library, models))
    assert row.status == "error" and _generated(session) == []


def test_a_restart_resumes_the_suites_job_instead_of_submitting_again(
        library, session, monkeypatch, media, models):
    job_id = _start(library, models)
    row = _row(session, job_id)
    # What a worker that died after submitting leaves behind.
    payload = json.loads(row.payload)
    payload["fal_request"] = {"suite_job_id": "sj-1"}
    payload["submitted_at"] = run_module.now().isoformat()
    row.payload = json.dumps(payload)
    session.add(row)
    session.commit()

    row = run_job(session, monkeypatch, job_id)
    assert row.status == "done", row.error_message
    assert media.submitted == []  # nothing was submitted, so nothing is billed twice


def test_a_queued_job_cancelled_with_no_worker_is_cancelled_at_the_suite(
        library, session, monkeypatch, media, models):
    job_id = _start(library, models)
    row = _row(session, job_id)
    payload = json.loads(row.payload)
    payload["fal_request"] = {"suite_job_id": "sj-1"}
    row.payload = json.dumps(payload)
    session.add(row)
    session.commit()

    run_module.cancel_remote(session, row)
    assert media.cancels == 1


def test_handles_are_read_by_the_mode_that_wrote_them():
    assert fal_backend.handle_from_dict({"suite_job_id": "x"}) == fal_backend.SuiteHandle("x")
    queued = {"request_id": "r", "status_url": "s", "response_url": "p", "cancel_url": "c"}
    assert isinstance(fal_backend.handle_from_dict(queued), fal.QueuedRequest)
    assert fal_backend.handle_from_dict(None) is None


def test_local_mode_still_needs_the_users_own_key(library, session, monkeypatch, models):
    """The other half: with the local backend nothing has changed."""
    res = library.post("/api/generate", json={"model_id": models[T2I].id, "prompt": "x"})
    assert res.status_code == 400 and res.json()["detail"]["code"] == "fal_key_missing"
