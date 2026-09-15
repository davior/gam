"""Running one action over a selection.

The behaviours that matter are the ones that stop a bulk run being worse than doing it
by hand: it must not keep spending after the provider starts refusing, it must not act on
assets that are not yours because the ids came from a browser, and one bad asset must not
end the run.
"""

import json

import httpx
import pytest
from sqlmodel import select

from app.enrichment import bulk
from app.jobs import enrichment as enrichment_jobs
from app.models.asset import Asset
from app.models.job import EnrichmentJob, KIND_BULK_ENRICH
from app.providers import _upstream

from tests.test_summarize import _upload_image, configure_provider

TEST_USER = "user-under-test"


@pytest.fixture(name="upstream")
def upstream_fixture(monkeypatch):
    state = {"calls": 0, "fail_from": None, "fail_on": set(), "status": 500}

    def fake_post_json(url, *, headers=None, json_body, timeout, label, **kwargs):
        state["calls"] += 1
        failing = state["fail_from"] is not None and state["calls"] >= state["fail_from"]
        if failing or state["calls"] in state["fail_on"]:
            return httpx.Response(state["status"], json={"error": {"message": "nope"}})
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "A summary of the thing."}],
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
        )

    monkeypatch.setattr(_upstream, "post_json", fake_post_json)
    return state


def run_job(library, session, monkeypatch, action, asset_ids):
    response = library.post(
        "/api/assets/bulk/enrich", json={"action": action, "asset_ids": asset_ids}
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["id"]

    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job_id)
    session.expire_all()
    return session.get(EnrichmentJob, job_id)


# ─── the payload ─────────────────────────────────────────────────────────────


def test_a_payload_round_trips():
    action, ids = bulk.decode(bulk.encode("summarize", ["a", "b"]))

    assert action == "summarize"
    assert ids == ["a", "b"]


@pytest.mark.parametrize(
    "payload",
    [None, "", "not json", "[1,2]", json.dumps({"action": "transcribe", "asset_ids": []})],
)
def test_an_unusable_payload_raises_rather_than_running_nothing(payload):
    """"Nothing, successfully" is the worst available answer for a job that was asked to
    do something."""
    with pytest.raises(ValueError):
        bulk.decode(payload)


def test_transcription_is_not_a_bulk_action():
    """Billed per minute of audio, so a mis-click over two hundred videos is an expensive
    way to learn it was available."""
    assert "transcribe" not in bulk.ACTIONS


# ─── running it ──────────────────────────────────────────────────────────────


def test_it_summarises_the_whole_selection(library, session, monkeypatch, upstream):
    first = _upload_image(library)
    second = _upload_image(library)
    configure_provider(session)

    job = run_job(library, session, monkeypatch, "summarize", [first["id"], second["id"]])

    assert job.status == "done"
    assert "2 done" in job.detail
    assert session.get(Asset, first["id"]).summary == "A summary of the thing."
    assert session.get(Asset, second["id"]).summary == "A summary of the thing."


def test_an_asset_that_is_not_yours_is_skipped_not_acted_on(library, session, monkeypatch, upstream):
    """The ids come from the browser, so ownership is checked per asset. A selection is
    not a capability."""
    mine = _upload_image(library)
    session.add(
        Asset(id="theirs", user_id="a-different-user", name="Theirs", asset_type="image")
    )
    session.commit()
    configure_provider(session)

    job = run_job(library, session, monkeypatch, "summarize", [mine["id"], "theirs"])

    assert job.status == "done"
    assert "1 no longer there" in job.detail
    session.expire_all()
    assert session.get(Asset, "theirs").summary is None


def test_a_deleted_asset_is_skipped_rather_than_failing_the_run(library, session, monkeypatch, upstream):
    first = _upload_image(library)
    configure_provider(session)

    job = run_job(library, session, monkeypatch, "summarize", [first["id"], "gone-already"])

    assert job.status == "done"
    assert "1 done" in job.detail


def test_one_bad_asset_does_not_end_the_run(library, session, monkeypatch, upstream):
    """A single pathological asset must not cost the other ninety-nine."""
    good = _upload_image(library)
    bad = _upload_image(library)
    # The second asset has nothing readable: no bytes, no transcript.
    broken = session.get(Asset, bad["id"])
    broken.storage_key = None
    broken.thumb_key = None
    broken.asset_type = "document"
    session.commit()
    configure_provider(session)

    job = run_job(library, session, monkeypatch, "summarize", [good["id"], bad["id"]])

    assert job.status == "done"
    assert "1 done" in job.detail
    assert "1 failed" in job.detail


def test_it_stops_once_the_provider_is_clearly_refusing(library, session, monkeypatch, upstream):
    """A rejected key fails identically on every asset. Burning through two hundred
    requests to discover that is slow, expensive and rude to the provider."""
    assets = [_upload_image(library)["id"] for _ in range(6)]
    configure_provider(session)
    upstream["fail_from"] = 1

    job = run_job(library, session, monkeypatch, "summarize", assets)

    assert job.status == "error"
    assert "failed in a row" in job.error_message
    # Stopped at the cutoff rather than trying all six.
    assert upstream["calls"] == bulk.CONSECUTIVE_FAILURE_LIMIT


def test_failures_that_are_not_consecutive_do_not_stop_the_run(library, session, monkeypatch, upstream):
    """Three failures *in a row* indict the provider; three spread out are just three
    awkward assets, and stopping on those would make the feature useless on a real
    library."""
    assets = [_upload_image(library)["id"] for _ in range(6)]
    configure_provider(session)
    # Fail, fail, succeed, fail, fail, succeed — never three in a row.
    upstream["fail_on"] = {1, 2, 4, 5}

    job = run_job(library, session, monkeypatch, "summarize", assets)

    assert job.status == "done"
    assert "2 done" in job.detail
    assert "4 failed" in job.detail
    # Every asset was attempted rather than the run stopping early.
    assert upstream["calls"] == 6


def test_autotag_over_a_selection_proposes_without_applying(library, session, monkeypatch, upstream):
    from app.models.suggestion import Suggestion
    from app.models.tag import Tag

    created = _upload_image(library)
    configure_provider(session)

    def reply(url, *, headers=None, json_body, timeout, label, **kwargs):
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": '{"title": "A title", "tags": ["one", "two"]}'}
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    monkeypatch.setattr(_upstream, "post_json", reply)
    job = run_job(library, session, monkeypatch, "autotag", [created["id"]])

    assert job.status == "done"
    assert len(session.exec(select(Suggestion)).all()) == 3
    # FR 9.1.4 holds in bulk exactly as it does one at a time.
    assert session.exec(select(Tag)).all() == []


# ─── the endpoint ────────────────────────────────────────────────────────────


def test_an_action_that_is_not_bulkable_is_refused(library, session):
    created = _upload_image(library)
    configure_provider(session)

    response = library.post(
        "/api/assets/bulk/enrich",
        json={"action": "transcribe", "asset_ids": [created["id"]]},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "bad_request"


def test_an_empty_selection_is_refused(library, session):
    configure_provider(session)

    response = library.post("/api/assets/bulk/enrich", json={"action": "summarize", "asset_ids": []})

    assert response.status_code == 400


def test_an_enormous_selection_is_refused(library, session):
    """The thing standing between a stray Ctrl-A and a four-figure bill."""
    configure_provider(session)

    response = library.post(
        "/api/assets/bulk/enrich",
        json={"action": "summarize", "asset_ids": [f"a{i}" for i in range(bulk.MAX_SELECTION + 1)]},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "selection_too_large"


def test_without_a_provider_it_says_why(library, session):
    created = _upload_image(library)

    response = library.post(
        "/api/assets/bulk/enrich", json={"action": "summarize", "asset_ids": [created["id"]]}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "provider_unavailable"


def test_bulk_embed_is_gated_on_the_embedding_provider_not_the_llm(library, session):
    """The one action with a different provider behind it. Requiring an LLM for it would
    be the wrong gate — and this is the SelectionBar embed M5 deferred."""
    created = _upload_image(library)
    configure_provider(session)  # an LLM, but no embedder

    response = library.post(
        "/api/assets/bulk/enrich", json={"action": "embed", "asset_ids": [created["id"]]}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "embedding_unavailable"


def test_only_one_bulk_run_at_a_time(library, session):
    created = _upload_image(library)
    configure_provider(session)
    library.post("/api/assets/bulk/enrich", json={"action": "summarize", "asset_ids": [created["id"]]})

    response = library.post(
        "/api/assets/bulk/enrich", json={"action": "describe", "asset_ids": [created["id"]]}
    )

    assert response.status_code == 409


def test_bulk_requires_authentication(client):
    response = client.post(
        "/api/assets/bulk/enrich", json={"action": "summarize", "asset_ids": ["a"]}
    )
    assert response.status_code == 401


def test_the_job_row_reads_as_a_library_job(library, session):
    """No single asset, so the activity feed says "Your whole library" rather than
    naming one arbitrarily."""
    created = _upload_image(library)
    configure_provider(session)

    body = library.post(
        "/api/assets/bulk/enrich", json={"action": "summarize", "asset_ids": [created["id"]]}
    ).json()["data"]

    assert body["asset_id"] is None
    assert body["action"] == KIND_BULK_ENRICH
