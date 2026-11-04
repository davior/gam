"""Transcription through Gecko Suite (`AI_BACKEND=suite`), with the suite faked.

ffmpeg really extracts the audio and the segments really are stored; what is faked is the
suite's end. These check who is asked, what is sent, that GAM writes no usage row of its
own, that the upload chain asks the suite whether a key exists (and treats a suite that is
down as "no"), and that the suite's own sentences reach the activity feed.
"""

import json
from pathlib import Path
from typing import List

import httpx
import pytest
from sqlmodel import select

from app import suite
from app.config import settings
from app.enrichment import deepgram
from app.enrichment.transcribe import TranscriptionError, run as run_transcribe
from app.media_tools import ffmpeg_available
from app.models.asset import Asset
from app.models.job import KIND_TRANSCRIBE, EnrichmentJob
from app.models.transcript import TranscriptSegment
from app.models.usage import UsageEvent
from tests.test_suite_backend import SECRET
from tests.test_transcripts import _upload_audio, _upload_video

FIXTURES = Path(__file__).parent / "fixtures"
TEST_USER = "user-under-test"

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def _suite_transcript() -> dict:
    """What the suite answers: the Deepgram fixture, parsed into the suite's shape."""
    parsed = deepgram.parse_response(json.loads((FIXTURES / "deepgram_response.json").read_text()))
    return {
        "segments": [{"text": s.text, "start": s.start, "end": s.end, "speaker": s.speaker,
                      "words": [{"text": w.text, "start": w.start, "end": w.end} for w in s.words]}
                     for s in parsed.segments],
        "language": parsed.language, "model": parsed.model, "duration_seconds": 12.0,
        "cost": None, "currency": None, "usage_event_id": "u"}


class FakeSuiteSpeech:
    def __init__(self):
        self.requests: List[httpx.Request] = []
        self.ready = True
        self.down = False
        self.stt_response = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused")
        self.requests.append(request)
        if request.method == "GET" and request.url.path == "/api/v1/ai/speech/stt/ready":
            return httpx.Response(200, json={"data": {"ready": self.ready, "provider": "Deepgram"}})
        if request.method == "POST" and request.url.path == "/api/v1/ai/speech/stt":
            if self.stt_response is not None:
                return self.stt_response
            return httpx.Response(200, json={"data": _suite_transcript()})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    def posts(self):
        return [r for r in self.requests if r.method == "POST"]


@pytest.fixture(name="speech")
def speech_fixture(monkeypatch):
    fake = FakeSuiteSpeech()
    monkeypatch.setattr(settings, "ai_backend", "suite")
    monkeypatch.setattr(settings, "suite_app_secret", SECRET)
    monkeypatch.setattr(suite, "TRANSPORT", httpx.MockTransport(fake.handler))
    return fake


def _jobs(session, asset_id):
    session.expire_all()
    return session.exec(select(EnrichmentJob).where(EnrichmentJob.asset_id == asset_id)).all()


@needs_ffmpeg
def test_a_transcription_runs_on_the_suite_with_no_deepgram_key_here(library, session, speech):
    asset = session.get(Asset, _upload_video(library)["id"])
    reported = []
    count = run_transcribe(session, asset, lambda stage, pct, detail="": reported.append(stage))

    assert count == 2
    segments = session.exec(select(TranscriptSegment).where(TranscriptSegment.asset_id == asset.id)).all()
    assert len(segments) == 2 and "deploying nano weapons" in segments[0].text
    session.refresh(asset)
    assert asset.transcript_status == "done" and asset.transcript_model == "nova-3"

    # The audio went as the body, as GAM for the owner, attributed to the asset.
    (post,) = speech.posts()
    assert post.headers["x-gecko-app"] == "gam" and post.headers["x-on-behalf-of"] == TEST_USER
    assert "authorization" not in post.headers and post.headers["content-type"] == "audio/flac"
    assert post.content[:4] == b"fLaC"
    query = dict(post.url.params)
    assert query["subject_ref"] == f"gam:asset:{asset.id}" and query["app"] == "gam"
    assert "Transcribing" in reported

    # The suite recorded the seconds. A row here too would count the transcription twice.
    assert session.exec(select(UsageEvent)).all() == []


@needs_ffmpeg
def test_the_suites_own_sentence_is_what_the_job_says(library, session, speech):
    speech.stt_response = httpx.Response(409, json={"detail": {
        "code": "ai_not_configured",
        "message": "No Deepgram key is set up. Add a Deepgram provider in Gecko Suite."}})
    asset = session.get(Asset, _upload_video(library)["id"])
    with pytest.raises(TranscriptionError, match="Add a Deepgram provider in Gecko Suite"):
        run_transcribe(session, asset, lambda *_a, **_k: None)
    session.refresh(asset)
    assert asset.transcript_status != "done"


@needs_ffmpeg
def test_a_suite_that_is_down_fails_the_job_with_a_sentence(library, session, speech):
    asset = session.get(Asset, _upload_video(library)["id"])
    speech.down = True
    with pytest.raises(TranscriptionError, match="can't be reached"):
        run_transcribe(session, asset, lambda *_a, **_k: None)


@needs_ffmpeg
def test_a_recording_too_long_for_the_suite_says_so(library, session, speech):
    speech.stt_response = httpx.Response(413, json={"detail": {
        "code": "audio_too_large", "message": "That audio is over 300 MB. Send it in parts."}})
    asset = session.get(Asset, _upload_video(library)["id"])
    with pytest.raises(TranscriptionError, match="over 300 MB"):
        run_transcribe(session, asset, lambda *_a, **_k: None)


# ─── whether to transcribe on upload ─────────────────────────────────────────


@needs_ffmpeg
def test_an_upload_is_transcribed_when_the_suite_has_a_key(library, session, speech):
    created = _upload_video(library)
    (job,) = _jobs(session, created["id"])
    assert job.kind == KIND_TRANSCRIBE and job.status == "queued"
    # Asked as GAM for the uploader, before any work was queued.
    assert any(r.url.path == "/api/v1/ai/speech/stt/ready" for r in speech.requests)


@needs_ffmpeg
def test_an_upload_queues_nothing_when_the_suite_has_no_key(library, session, speech):
    speech.ready = False
    assert _jobs(session, _upload_audio(library)["id"]) == []


@needs_ffmpeg
def test_an_upload_still_succeeds_when_the_suite_is_down(library, session, speech):
    speech.down = True
    created = _upload_video(library)
    # No red row after every upload: the library works, and Transcribe can be pressed later.
    assert _jobs(session, created["id"]) == []
    assert library.get(f"/api/assets/{created['id']}").status_code == 200


def test_local_mode_still_asks_this_apps_own_key(library, session):
    """The other half: nothing changed without the switch."""
    from app.settings_store import DEEPGRAM_API_KEY, set_setting

    assert _jobs(session, _upload_video(library)["id"]) == []
    set_setting(session, TEST_USER, DEEPGRAM_API_KEY, "dg-test-key")
    created = _upload_video(library)
    assert [j.kind for j in _jobs(session, created["id"])] == [KIND_TRANSCRIBE]
