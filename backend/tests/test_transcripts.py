"""Transcription end to end, with Deepgram stubbed at the client boundary.

Everything below that boundary is real: the job row, the worker dispatch, ffmpeg audio
extraction from actual media, segment storage, and the API. Only the upstream HTTP call
is replaced — it needs a key and a live account, and it is already covered separately
in test_deepgram.py.
"""

import json
from pathlib import Path

import pytest
from sqlmodel import select

from app.enrichment import deepgram
from app.enrichment.transcribe import run as run_transcribe
from app.models.asset import Asset
from app.models.job import EnrichmentJob, KIND_TRANSCRIBE
from app.models.transcript import TranscriptSegment
from app.settings_store import DEEPGRAM_API_KEY, set_setting
from app.media_tools import ffmpeg_available

FIXTURES = Path(__file__).parent / "fixtures"

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def _upload_video(client):
    return client.post(
        "/api/assets",
        files=[("files", ("sample_video.mp4", (FIXTURES / "sample_video.mp4").read_bytes(), "video/mp4"))],
    ).json()["created"][0]


def _upload_image(client):
    return client.post(
        "/api/assets",
        files=[("files", ("sample_image.jpg", (FIXTURES / "sample_image.jpg").read_bytes(), "image/jpeg"))],
    ).json()["created"][0]


def _fake_transcript() -> deepgram.Transcript:
    payload = json.loads((FIXTURES / "deepgram_response.json").read_text())
    return deepgram.parse_response(payload)


@pytest.fixture(name="stub_deepgram")
def stub_deepgram_fixture(monkeypatch):
    """Replace only the upstream call, keeping the real parse."""
    calls = []

    def fake_transcribe_file(audio_path, api_key, *, model=deepgram.DEFAULT_MODEL, language=None):
        # The audio really was extracted by ffmpeg before this point.
        calls.append({"path": Path(audio_path), "key": api_key, "model": model})
        assert Path(audio_path).is_file(), "the worker sent a file that does not exist"
        return _fake_transcript()

    monkeypatch.setattr(deepgram, "transcribe_file", fake_transcribe_file)
    # transcribe.py imports the module, not the function, so patching the module
    # attribute is enough — but assert that, rather than assuming it.
    from app.enrichment import transcribe as transcribe_module

    assert transcribe_module.deepgram is deepgram
    return calls


# ─── starting a job ──────────────────────────────────────────────────────────


def test_transcribe_queues_a_job(library):
    asset = _upload_video(library)

    response = library.post(f"/api/assets/{asset['id']}/transcribe")
    assert response.status_code == 202

    job = response.json()["data"]
    assert job["action"] == "transcribe"
    assert job["status"] == "queued"
    assert job["asset_id"] == asset["id"]


def test_transcribe_refuses_an_image(library):
    asset = _upload_image(library)

    response = library.post(f"/api/assets/{asset['id']}/transcribe")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "not_transcribable"


def test_transcribe_refuses_a_second_concurrent_run(library):
    """Two runs would race to replace the same segments, and bill twice for the same
    audio."""
    asset = _upload_video(library)
    library.post(f"/api/assets/{asset['id']}/transcribe")

    response = library.post(f"/api/assets/{asset['id']}/transcribe")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "already_running"


def test_transcribe_someone_elses_asset_is_a_404(library, session):
    other = Asset(user_id="somebody-else", name="Theirs", asset_type="video", source="local_upload")
    session.add(other)
    session.commit()

    assert library.post(f"/api/assets/{other.id}/transcribe").status_code == 404


# ─── the job itself ──────────────────────────────────────────────────────────


@needs_ffmpeg
def test_running_the_job_stores_segments(library, session, stub_deepgram):
    created = _upload_video(library)
    set_setting(session, "user-under-test", DEEPGRAM_API_KEY, "dg-test-key")

    asset = session.get(Asset, created["id"])
    reported = []
    count = run_transcribe(session, asset, lambda stage, pct, detail="": reported.append(stage))

    assert count == 2
    assert stub_deepgram, "the client was never called"

    segments = session.exec(
        select(TranscriptSegment).where(TranscriptSegment.asset_id == asset.id)
    ).all()
    assert len(segments) == 2
    assert "deploying nano weapons" in segments[0].text

    # Progress was reported, which is also where cancellation is checked.
    assert "Extracting audio" in reported
    assert "Transcribing" in reported


@needs_ffmpeg
def test_the_job_records_what_produced_the_transcript(library, session, stub_deepgram):
    created = _upload_video(library)
    set_setting(session, "user-under-test", DEEPGRAM_API_KEY, "dg-test-key")

    asset = session.get(Asset, created["id"])
    run_transcribe(session, asset, lambda *_a, **_k: None)

    session.refresh(asset)
    assert asset.transcript_status == "done"
    assert asset.transcript_model == "nova-3"
    assert asset.transcript_language == "en"


@needs_ffmpeg
def test_word_timings_survive_storage(library, session, stub_deepgram):
    """The whole point of the feature: seek to the word, not the file."""
    created = _upload_video(library)
    set_setting(session, "user-under-test", DEEPGRAM_API_KEY, "dg-test-key")

    asset = session.get(Asset, created["id"])
    run_transcribe(session, asset, lambda *_a, **_k: None)

    first = session.exec(
        select(TranscriptSegment)
        .where(TranscriptSegment.asset_id == asset.id)
        .order_by(TranscriptSegment.idx)
    ).first()

    words = json.loads(first.words)
    assert len(words) == 10
    deploying = next(w for w in words if w["w"] == "deploying")
    assert deploying["s"] == pytest.approx(1.2)


def test_the_job_refuses_without_a_key(library, session):
    """The one failure a user can fix, so it says what to do."""
    created = _upload_video(library)
    asset = session.get(Asset, created["id"])

    from app.enrichment.transcribe import TranscriptionError

    with pytest.raises(TranscriptionError, match="No Deepgram API key"):
        run_transcribe(session, asset, lambda *_a, **_k: None)


# ─── reading the transcript ──────────────────────────────────────────────────


@needs_ffmpeg
def test_reading_a_transcript(library, session, stub_deepgram):
    created = _upload_video(library)
    set_setting(session, "user-under-test", DEEPGRAM_API_KEY, "dg-test-key")
    run_transcribe(session, session.get(Asset, created["id"]), lambda *_a, **_k: None)

    body = library.get(f"/api/assets/{created['id']}/transcript").json()["data"]

    assert body["status"] == "done"
    assert body["model"] == "nova-3"
    assert len(body["segments"]) == 2
    first = body["segments"][0]
    assert first["start_time"] == pytest.approx(0.08)
    assert first["speaker"] == 0
    assert first["words"][0]["text"] == "We"


def test_reading_a_transcript_that_does_not_exist_yet(library):
    created = _upload_video(library)

    body = library.get(f"/api/assets/{created['id']}/transcript").json()["data"]
    assert body["segments"] == []
    assert body["status"] is None


# ─── correcting a segment ────────────────────────────────────────────────────


@needs_ffmpeg
def test_editing_a_segment_marks_it(library, session, stub_deepgram):
    created = _upload_video(library)
    set_setting(session, "user-under-test", DEEPGRAM_API_KEY, "dg-test-key")
    run_transcribe(session, session.get(Asset, created["id"]), lambda *_a, **_k: None)

    segment_id = library.get(f"/api/assets/{created['id']}/transcript").json()["data"]["segments"][0]["id"]

    response = library.patch(
        f"/api/assets/{created['id']}/transcript/{segment_id}",
        json={"text": "We are looking at deploying nanoweapons via aerosol dispersion."},
    )
    assert response.status_code == 200

    segment = response.json()["data"]
    assert segment["edited"] is True
    assert "nanoweapons" in segment["text"]
    # The machine's word timings described the machine's wording. Once a person
    # rewrites it they no longer do, and keeping them would highlight the wrong words.
    assert segment["words"] == []


@needs_ffmpeg
def test_a_rerun_keeps_hand_corrections(library, session, stub_deepgram):
    """A re-run should improve a transcript, not cost the user every correction they
    have made — which is enough to stop them re-running it at all."""
    created = _upload_video(library)
    set_setting(session, "user-under-test", DEEPGRAM_API_KEY, "dg-test-key")
    asset = session.get(Asset, created["id"])
    run_transcribe(session, asset, lambda *_a, **_k: None)

    segment_id = library.get(f"/api/assets/{created['id']}/transcript").json()["data"]["segments"][0]["id"]
    library.patch(
        f"/api/assets/{created['id']}/transcript/{segment_id}",
        json={"text": "CORRECTED BY HAND"},
    )

    run_transcribe(session, asset, lambda *_a, **_k: None)

    segments = library.get(f"/api/assets/{created['id']}/transcript").json()["data"]["segments"]
    assert segments[0]["text"] == "CORRECTED BY HAND"
    assert segments[0]["edited"] is True
    # And the untouched segment was refreshed from the new run.
    assert len(segments) == 2


@needs_ffmpeg
def test_a_segment_cannot_end_before_it_starts(library, session, stub_deepgram):
    created = _upload_video(library)
    set_setting(session, "user-under-test", DEEPGRAM_API_KEY, "dg-test-key")
    run_transcribe(session, session.get(Asset, created["id"]), lambda *_a, **_k: None)

    segment_id = library.get(f"/api/assets/{created['id']}/transcript").json()["data"]["segments"][0]["id"]

    response = library.patch(
        f"/api/assets/{created['id']}/transcript/{segment_id}",
        json={"start_time": 90.0, "end_time": 10.0},
    )
    assert response.status_code == 400


def test_editing_a_segment_of_someone_elses_asset_is_a_404(library, session):
    other = Asset(user_id="somebody-else", name="Theirs", asset_type="video", source="local_upload")
    session.add(other)
    session.commit()
    segment = TranscriptSegment(asset_id=other.id, user_id="somebody-else", idx=0, text="secret")
    session.add(segment)
    session.commit()

    assert library.patch(
        f"/api/assets/{other.id}/transcript/{segment.id}", json={"text": "x"}
    ).status_code == 404


# ─── activity ────────────────────────────────────────────────────────────────


def test_activity_lists_the_job(library):
    asset = _upload_video(library)
    library.post(f"/api/assets/{asset['id']}/transcribe")

    body = library.get("/api/activity").json()
    assert body["total"] == 1
    assert body["data"][0]["action"] == "transcribe"


def test_activity_can_filter_to_active_jobs(library, session):
    asset = _upload_video(library)
    library.post(f"/api/assets/{asset['id']}/transcribe")

    job = session.exec(select(EnrichmentJob)).first()
    job.status = "done"
    session.add(job)
    session.commit()

    assert library.get("/api/activity", params={"active": True}).json()["total"] == 0
    assert library.get("/api/activity").json()["total"] == 1


def test_cancelling_a_job(library):
    asset = _upload_video(library)
    job = library.post(f"/api/assets/{asset['id']}/transcribe").json()["data"]

    response = library.delete(f"/api/activity/enrichment/{job['id']}")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "cancelled"


def test_cancelling_lets_a_new_run_start(library):
    """The 409 guard keys off active jobs, so cancelling has to actually release it."""
    asset = _upload_video(library)
    job = library.post(f"/api/assets/{asset['id']}/transcribe").json()["data"]
    library.delete(f"/api/activity/enrichment/{job['id']}")

    assert library.post(f"/api/assets/{asset['id']}/transcribe").status_code == 202


def test_activity_only_shows_your_own_jobs(library, session):
    session.add(
        EnrichmentJob(user_id="somebody-else", asset_id="x", kind=KIND_TRANSCRIBE, asset_name="Theirs")
    )
    session.commit()

    assert library.get("/api/activity").json()["total"] == 0


def test_activity_requires_authentication(client):
    assert client.get("/api/activity").status_code == 401


# ─── cancellation through the queue rather than the API ──────────────────────


@needs_ffmpeg
def test_a_queue_cancel_still_leaves_a_terminal_row(library, session, monkeypatch):
    """Cancelling without marking the row first must still end it.

    Two paths reach the worker's cancellation handler. The API marks the row
    "cancelled" and then signals the queue, so the row is already terminal. The stale
    sweeper — and any direct queue.cancel() — only signals, and the worker used to
    assume the caller had done the marking. A row left at "processing" then sits there
    until the sweeper ends it forty minutes later, which reads as a hang.

    Found by a live run, not by the suite, which is why it is pinned here.
    """
    import threading

    from app.jobs import enrichment as enrichment_jobs
    from app.jobs.runner import JobCancelled

    created = _upload_video(library)
    set_setting(session, "user-under-test", DEEPGRAM_API_KEY, "dg-test-key")

    job = EnrichmentJob(
        user_id="user-under-test",
        asset_id=created["id"],
        kind=KIND_TRANSCRIBE,
        asset_name="Interview",
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    job_id = job.id

    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())

    # Cancel before the worker starts, so the first progress checkpoint raises.
    queue.cancel(job_id)
    try:
        enrichment_jobs._run_job(job_id)
    except JobCancelled:  # pragma: no cover - the handler should swallow it
        pytest.fail("JobCancelled escaped the worker")
    finally:
        queue._cancelled.discard(job_id)

    session.expire_all()
    ended = session.get(EnrichmentJob, job_id)
    assert ended.status == "cancelled", f"left at {ended.status!r}"

    asset = session.get(Asset, created["id"])
    assert asset.transcript_status != "running", "the asset was left spinning"


@needs_ffmpeg
def test_the_api_cancel_status_is_not_overwritten(library, session, monkeypatch):
    """The guard must not clobber a row the API already marked."""
    from app.jobs import enrichment as enrichment_jobs

    created = _upload_video(library)
    set_setting(session, "user-under-test", DEEPGRAM_API_KEY, "dg-test-key")

    job = library.post(f"/api/assets/{created['id']}/transcribe").json()["data"]
    library.delete(f"/api/activity/enrichment/{job['id']}")

    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job["id"])

    session.expire_all()
    assert session.get(EnrichmentJob, job["id"]).status == "cancelled"
