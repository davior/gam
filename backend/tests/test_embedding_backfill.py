"""Embedding a library that already existed.

Transcription embeds what it produces, so content ingested after a provider is
configured looks after itself. Everything ingested before is invisible to semantic
search until something walks it — which, on an instance that has been running a while,
is the whole library.
"""

from pathlib import Path

import pytest
from sqlmodel import select

from app import embeddings
from app.embeddings import PROVIDER_OLLAMA
from app.models.asset import Asset
from app.models.embedding import Embedding, OWNER_ASSET
from app.models.job import EnrichmentJob, KIND_BACKFILL_EMBEDDINGS
from app.models.transcript import TranscriptSegment
from app.settings_store import EMBEDDING_PROVIDER, set_setting

FIXTURES = Path(__file__).parent / "fixtures"
TEST_USER = "user-under-test"


class StubEmbedder:
    """Deterministic and offline. Kept local rather than in conftest for the reason
    test_embed_jobs.py gives: a provider stub in scope for the whole suite would hide
    the unconfigured path that most of these tests depend on."""

    model = "stub-embed-v1"
    dimensions = 2

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]


@pytest.fixture(name="embedder")
def embedder_fixture(monkeypatch, session):
    stub = StubEmbedder()
    build = lambda *_a, **_k: stub  # noqa: E731

    # `build_embedder` is symbol-imported into several modules, so each holds its own
    # reference and patching the definition alone leaves them on the real one — which
    # is how this fixture first presented as "connection refused" against a localhost
    # Ollama nobody was running. Patch every binding the backfill path actually touches.
    from app.enrichment import embed as embed_module
    from app.routers import embeddings as embeddings_router

    monkeypatch.setattr(embeddings, "build_embedder", build)
    monkeypatch.setattr(embed_module, "build_embedder", build)
    monkeypatch.setattr(embeddings_router, "build_embedder", build)

    set_setting(session, TEST_USER, EMBEDDING_PROVIDER, PROVIDER_OLLAMA)
    return stub


def make_asset(session, name="Giordano interview", **extra):
    asset = Asset(
        user_id=TEST_USER, name=name, asset_type="video", source="local_upload", **extra
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def add_segments(session, asset, count):
    for index in range(count):
        session.add(
            TranscriptSegment(
                asset_id=asset.id,
                user_id=TEST_USER,
                idx=index,
                text=f"segment {index}",
                start_time=float(index),
                end_time=float(index + 1),
            )
        )
    session.commit()


def mark_embedded(session, asset, model="stub-embed-v1"):
    session.add(
        Embedding(
            owner_kind=OWNER_ASSET,
            owner_id=asset.id,
            asset_id=asset.id,
            user_id=TEST_USER,
            model=model,
            dim=2,
            vector=b"\x00" * 8,
        )
    )
    session.commit()


# ─── coverage ────────────────────────────────────────────────────────────────


def test_an_untouched_library_is_entirely_pending(library, session, embedder):
    make_asset(session, name="One")
    make_asset(session, name="Two")

    body = library.get("/api/embeddings/status").json()["data"]

    assert body["total_assets"] == 2
    assert body["pending_assets"] == 2
    assert body["embedded_assets"] == 0
    assert body["model"] == "stub-embed-v1"


def test_an_embedded_asset_is_not_pending(library, session, embedder):
    asset = make_asset(session)
    mark_embedded(session, asset)

    body = library.get("/api/embeddings/status").json()["data"]

    assert body["pending_assets"] == 0
    assert body["embedded_assets"] == 1


def test_changing_the_model_makes_everything_pending_again(library, session, embedder):
    """The predicate that matters. `vectors.search` refuses a matrix built by another
    model, so a vector from a previous provider is not a partial answer — the asset
    holding it is exactly as invisible as one never embedded."""
    asset = make_asset(session)
    mark_embedded(session, asset, model="some-older-model")

    body = library.get("/api/embeddings/status").json()["data"]

    assert body["pending_assets"] == 1
    assert body["embedded_assets"] == 0


def test_pending_segments_counts_only_pending_assets(library, session, embedder):
    """Segments are the real unit of work — assets alone would call a 3-asset backfill
    quick when it is twenty minutes of provider calls."""
    pending = make_asset(session, name="Not yet")
    add_segments(session, pending, 5)

    done = make_asset(session, name="Already done")
    add_segments(session, done, 100)
    mark_embedded(session, done)

    body = library.get("/api/embeddings/status").json()["data"]

    assert body["pending_segments"] == 5


def test_another_users_assets_are_not_counted(library, session, embedder):
    session.add(Asset(user_id="somebody-else", name="Theirs", asset_type="image", source="local_upload"))
    session.commit()

    assert library.get("/api/embeddings/status").json()["data"]["total_assets"] == 0


def test_status_without_a_provider_is_a_400(library, session):
    assert library.get("/api/embeddings/status").status_code == 400


def test_status_requires_authentication(client):
    assert client.get("/api/embeddings/status").status_code == 401


# ─── starting a backfill ─────────────────────────────────────────────────────


def test_backfill_queues_one_job_not_one_per_asset(library, session, embedder):
    """The deciding reason for the whole-library shape: cancel stops one row, so
    per-asset jobs would mean clicking Cancel once per asset."""
    for index in range(5):
        make_asset(session, name=f"Asset {index}")

    response = library.post("/api/embeddings/backfill")

    assert response.status_code == 202
    body = response.json()["data"]
    assert body["action"] == KIND_BACKFILL_EMBEDDINGS
    assert body["asset_id"] is None
    assert len(session.exec(select(EnrichmentJob)).all()) == 1


def test_a_second_backfill_is_a_409(library, session, embedder):
    make_asset(session)
    assert library.post("/api/embeddings/backfill").status_code == 202

    second = library.post("/api/embeddings/backfill")

    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "already_running"


def test_backfill_without_a_provider_is_a_400(library, session):
    make_asset(session)

    response = library.post("/api/embeddings/backfill")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "embedding_unavailable"
    assert session.exec(select(EnrichmentJob)).all() == []


def test_a_library_job_does_not_appear_in_an_assets_activity(library, session, embedder):
    """It has no asset, so it must not surface in a panel scoped to one."""
    asset = make_asset(session)
    library.post("/api/embeddings/backfill")

    scoped = library.get("/api/activity", params={"asset_id": asset.id}).json()["data"]

    assert scoped == []


# ─── running it ──────────────────────────────────────────────────────────────


def _run(job_id, session, monkeypatch):
    from app.jobs import enrichment as enrichment_jobs

    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job_id)
    session.expire_all()


def test_the_walk_embeds_every_pending_asset(library, session, embedder, monkeypatch):
    for index in range(3):
        make_asset(session, name=f"Asset {index}")

    job = library.post("/api/embeddings/backfill").json()["data"]
    _run(job["id"], session, monkeypatch)

    assert session.get(EnrichmentJob, job["id"]).status == "done"
    assert library.get("/api/embeddings/status").json()["data"]["pending_assets"] == 0


def test_running_it_twice_does_nothing_the_second_time(library, session, embedder, monkeypatch):
    """Idempotence, and the same property that makes a cancelled run resumable: the
    pending set is recomputed at the top of every run."""
    make_asset(session)

    first = library.post("/api/embeddings/backfill").json()["data"]
    _run(first["id"], session, monkeypatch)
    calls_after_first = embedder.calls

    second = library.post("/api/embeddings/backfill").json()["data"]
    _run(second["id"], session, monkeypatch)

    assert embedder.calls == calls_after_first
    assert session.get(EnrichmentJob, second["id"]).detail == "0 embedded"


def test_one_failing_asset_does_not_end_the_run(library, session, embedder, monkeypatch):
    """A 500-asset backfill must not die on asset seven because one file is odd."""
    from app.enrichment import backfill

    good_one = make_asset(session, name="Good one")
    bad = make_asset(session, name="Bad")
    good_two = make_asset(session, name="Good two")

    real_run = backfill.embed.run

    def sometimes_explodes(session_, asset, progress):
        if asset.id == bad.id:
            raise RuntimeError("this one is broken")
        return real_run(session_, asset, progress)

    monkeypatch.setattr(backfill.embed, "run", sometimes_explodes)

    job = library.post("/api/embeddings/backfill").json()["data"]
    _run(job["id"], session, monkeypatch)

    ended = session.get(EnrichmentJob, job["id"])
    assert ended.status == "done"
    assert "1 failed" in ended.detail
    # The other two still landed.
    assert session.exec(
        select(Embedding).where(Embedding.asset_id == good_one.id)
    ).first() is not None
    assert session.exec(
        select(Embedding).where(Embedding.asset_id == good_two.id)
    ).first() is not None


def test_a_run_of_failures_stops_the_backfill(library, session, embedder, monkeypatch):
    """A rejected key fails identically on every asset; burning five hundred requests
    to discover that is slow, expensive and rude to the provider."""
    from app.enrichment import backfill

    for index in range(10):
        make_asset(session, name=f"Asset {index}")

    def always_explodes(*_a, **_k):
        raise RuntimeError("provider says no")

    monkeypatch.setattr(backfill.embed, "run", always_explodes)

    job = library.post("/api/embeddings/backfill").json()["data"]
    _run(job["id"], session, monkeypatch)

    ended = session.get(EnrichmentJob, job["id"])
    assert ended.status == "error"
    assert "in a row" in ended.error_message


def test_cancelling_keeps_what_was_already_embedded(library, session, embedder, monkeypatch):
    from app.jobs import enrichment as enrichment_jobs

    for index in range(4):
        make_asset(session, name=f"Asset {index}")

    job = library.post("/api/embeddings/backfill").json()["data"]

    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    queue.cancel(job["id"])
    try:
        enrichment_jobs._run_job(job["id"])
    finally:
        queue._cancelled.discard(job["id"])

    session.expire_all()
    assert session.get(EnrichmentJob, job["id"]).status == "cancelled"
