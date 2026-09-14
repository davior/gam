"""Getting vectors written at all.

`KIND_EMBED` was dispatched by the worker from M5 onward, but nothing ever created a
job of that kind — no endpoint, no chain, no backfill. The branch was unreachable, so
the embedding table could only ever be filled by a test calling the worker directly,
and semantic search over real data had nothing to search.
"""

import json
from pathlib import Path

import pytest
from sqlmodel import select

from app.enrichment import deepgram

from app.embeddings import PROVIDER_OLLAMA
from app.media_tools import ffmpeg_available
from app.models.asset import Asset
from app.models.job import EnrichmentJob, KIND_EMBED
from app.settings_store import DEEPGRAM_API_KEY, EMBEDDING_PROVIDER, set_setting

FIXTURES = Path(__file__).parent / "fixtures"
TEST_USER = "user-under-test"

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def _upload_image(client):
    return client.post(
        "/api/assets",
        files=[
            (
                "files",
                ("sample_image.jpg", (FIXTURES / "sample_image.jpg").read_bytes(), "image/jpeg"),
            )
        ],
    ).json()["created"][0]


def _configure_embedding(session):
    """Ollama needs no key, so this is the cheapest way to have a configured provider."""
    set_setting(session, TEST_USER, EMBEDDING_PROVIDER, PROVIDER_OLLAMA)


def _jobs_of_kind(session, asset_id, kind):
    return session.exec(
        select(EnrichmentJob).where(
            EnrichmentJob.asset_id == asset_id, EnrichmentJob.kind == kind
        )
    ).all()


@pytest.fixture(name="stub_deepgram")
def stub_deepgram_fixture(monkeypatch):
    """Replace only the upstream call, keeping the real parse.

    A local copy of the fixture in test_transcripts.py rather than a shared one: these
    tests assert on what happens *after* a transcript lands, and hoisting it into
    conftest would put a Deepgram stub in scope for every test in the suite.
    """

    def fake_transcribe_file(audio_path, api_key, *, model=deepgram.DEFAULT_MODEL, language=None):
        assert Path(audio_path).is_file(), "the worker sent a file that does not exist"
        return deepgram.parse_response(json.loads((FIXTURES / "deepgram_response.json").read_text()))

    monkeypatch.setattr(deepgram, "transcribe_file", fake_transcribe_file)


# ─── the endpoint ────────────────────────────────────────────────────────────


def test_embed_queues_a_job(library, session):
    """An image never transcribes, so without this endpoint nothing would ever embed
    its name and description and it could not be found by meaning at all."""
    asset = _upload_image(library)
    _configure_embedding(session)

    response = library.post(f"/api/assets/{asset['id']}/embed")

    assert response.status_code == 202
    body = response.json()["data"]
    assert body["action"] == KIND_EMBED
    assert body["status"] == "queued"
    assert len(_jobs_of_kind(session, asset["id"], KIND_EMBED)) == 1


def test_embed_without_a_provider_is_a_400(library, session):
    """Rejected up front rather than queued and failed: a job row that exists only to
    turn red tells the user nothing the error message cannot."""
    asset = _upload_image(library)

    response = library.post(f"/api/assets/{asset['id']}/embed")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "embedding_unavailable"
    assert _jobs_of_kind(session, asset["id"], KIND_EMBED) == []


def test_embedding_twice_at_once_is_a_409(library, session):
    asset = _upload_image(library)
    _configure_embedding(session)

    assert library.post(f"/api/assets/{asset['id']}/embed").status_code == 202
    second = library.post(f"/api/assets/{asset['id']}/embed")

    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "already_running"
    assert len(_jobs_of_kind(session, asset["id"], KIND_EMBED)) == 1


def test_embed_someone_elses_asset_is_a_404(library, session):
    other = Asset(user_id="somebody-else", name="Theirs", asset_type="image", source="local_upload")
    session.add(other)
    session.commit()
    _configure_embedding(session)

    assert library.post(f"/api/assets/{other.id}/embed").status_code == 404


def test_embed_requires_authentication(client):
    assert client.post("/api/assets/whatever/embed").status_code == 401


# ─── the chain off transcription ─────────────────────────────────────────────


@needs_ffmpeg
def test_a_finished_transcript_queues_its_own_embedding(
    library, session, stub_deepgram, monkeypatch
):
    """A transcript nobody embeds is invisible to half of search, and nothing else in
    the app would ever ask for the embed job."""
    from app.jobs import enrichment as enrichment_jobs

    created = library.post(
        "/api/assets",
        files=[
            ("files", ("clip.mp4", (FIXTURES / "sample_video.mp4").read_bytes(), "video/mp4"))
        ],
    ).json()["created"][0]
    set_setting(session, TEST_USER, DEEPGRAM_API_KEY, "dg-test-key")
    _configure_embedding(session)

    job = library.post(f"/api/assets/{created['id']}/transcribe").json()["data"]

    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job["id"])

    session.expire_all()
    assert session.get(EnrichmentJob, job["id"]).status == "done"
    assert len(_jobs_of_kind(session, created["id"], KIND_EMBED)) == 1


@needs_ffmpeg
def test_no_provider_means_no_spurious_embed_job(library, session, stub_deepgram, monkeypatch):
    """Queueing unconditionally would put a red "no embedding provider" row in the
    activity feed after every single transcription, which trains people to ignore it."""
    from app.jobs import enrichment as enrichment_jobs

    created = library.post(
        "/api/assets",
        files=[
            ("files", ("clip.mp4", (FIXTURES / "sample_video.mp4").read_bytes(), "video/mp4"))
        ],
    ).json()["created"][0]
    set_setting(session, TEST_USER, DEEPGRAM_API_KEY, "dg-test-key")
    # Deliberately no embedding provider.

    job = library.post(f"/api/assets/{created['id']}/transcribe").json()["data"]

    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job["id"])

    session.expire_all()
    assert session.get(EnrichmentJob, job["id"]).status == "done", "the transcript must still land"
    assert _jobs_of_kind(session, created["id"], KIND_EMBED) == []


@needs_ffmpeg
def test_a_failed_transcript_queues_nothing(library, session, monkeypatch):
    from app.enrichment import deepgram
    from app.jobs import enrichment as enrichment_jobs

    created = library.post(
        "/api/assets",
        files=[
            ("files", ("clip.mp4", (FIXTURES / "sample_video.mp4").read_bytes(), "video/mp4"))
        ],
    ).json()["created"][0]
    set_setting(session, TEST_USER, DEEPGRAM_API_KEY, "dg-test-key")
    _configure_embedding(session)

    def explode(*_args, **_kwargs):
        raise deepgram.DeepgramError("upstream refused")

    monkeypatch.setattr(deepgram, "transcribe_file", explode)

    job = library.post(f"/api/assets/{created['id']}/transcribe").json()["data"]
    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job["id"])

    session.expire_all()
    assert session.get(EnrichmentJob, job["id"]).status == "error"
    assert _jobs_of_kind(session, created["id"], KIND_EMBED) == []
