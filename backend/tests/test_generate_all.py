"""The "Generate all" job: summarize, describe and autotag in one press.

Three real provider calls in a row, stubbed at the same upstream boundary the other
enrichment tests use — this is the "press each button in turn" behaviour turned into
one job, not a new prompt design, so what is worth proving is the sequencing: each
step's output is visible to the ones after it, and a failure partway through stops the
rest rather than running them against material that step already showed was unusable.
"""

import json

import httpx
import pytest

from app.enrichment import generate_all
from app.jobs import enrichment as enrichment_jobs
from app.models.asset import Asset
from app.models.job import EnrichmentJob, KIND_GENERATE_ALL
from app.providers import _upstream
from app.providers.base import ProviderError
from app.services import suggestions as suggestion_service

from tests.test_summarize import _upload_image, add_transcript, configure_provider

SUMMARY = "A long interview about neuroweapons, recorded in a studio."
DESCRIPTION = "James Giordano speaking to camera in a panelled studio, discussing DARPA."
SUGGESTIONS = {"title": "Giordano on Neuroweapons", "tags": ["James Giordano", "neuroweapons"]}

# In call order: summarize's plain text, describe's plain text, autotag's JSON.
REPLIES = [SUMMARY, DESCRIPTION, json.dumps(SUGGESTIONS)]


@pytest.fixture(name="upstream")
def upstream_fixture(monkeypatch):
    """Answer each of the three calls with its own canned reply, in order."""
    state = {"calls": [], "replies": list(REPLIES)}

    def fake_post_json(url, *, headers=None, json_body, timeout, label, **kwargs):
        state["calls"].append({"url": url, "body": json_body})
        text = state["replies"][len(state["calls"]) - 1]
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": text}],
                "usage": {"input_tokens": 500, "output_tokens": 30},
            },
        )

    monkeypatch.setattr(_upstream, "post_json", fake_post_json)
    return state


def sent_prompt(state, call_index: int) -> str:
    content = state["calls"][call_index]["body"]["messages"][0]["content"]
    return "\n".join(b["text"] for b in content if b.get("type") == "text")


# ─── the module, directly ────────────────────────────────────────────────────


def test_it_runs_all_three_and_writes_both_fields(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    add_transcript(session, asset.id, ["Giordano on neuroweapons."])
    configure_provider(session)

    generate_all.run(session, asset, lambda *a, **k: None)

    session.refresh(asset)
    assert asset.summary == SUMMARY
    assert asset.description == DESCRIPTION
    assert len(upstream["calls"]) == 3

    suggestions = suggestion_service.pending_for(session, asset.id)
    assert {s.value for s in suggestions} == {
        "Giordano on Neuroweapons",
        "James Giordano",
        "neuroweapons",
    }


def test_summarize_runs_before_describe_so_describe_can_avoid_repeating_it(
    library, session, upstream
):
    """`describe`'s own prompt knows to skip a summary that already exists. That only
    means anything if the summary is written before describe reads the asset."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    add_transcript(session, asset.id, ["Giordano on neuroweapons."])
    configure_provider(session)

    generate_all.run(session, asset, lambda *a, **k: None)

    describe_prompt = sent_prompt(upstream, 1)
    assert SUMMARY in describe_prompt
    assert "do not repeat it" in describe_prompt


def test_autotag_runs_last_and_sees_both_fields(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    add_transcript(session, asset.id, ["Giordano on neuroweapons."])
    configure_provider(session)

    generate_all.run(session, asset, lambda *a, **k: None)

    autotag_prompt = sent_prompt(upstream, 2)
    assert SUMMARY in autotag_prompt
    assert DESCRIPTION in autotag_prompt


def test_a_failure_partway_through_stops_the_rest(library, session, monkeypatch):
    """One clear error beats two of the three silently not happening."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    add_transcript(session, asset.id, ["Giordano on neuroweapons."])
    configure_provider(session)

    def refuse(url, **kwargs):
        return httpx.Response(401, json={"error": {"message": "credit balance is too low"}})

    monkeypatch.setattr(_upstream, "post_json", refuse)

    with pytest.raises(ProviderError, match="credit balance is too low"):
        generate_all.run(session, asset, lambda *a, **k: None)

    session.refresh(asset)
    assert asset.summary is None
    assert asset.description is None


# ─── the endpoint ────────────────────────────────────────────────────────────


def test_generate_all_queues_a_job(library, session):
    created = _upload_image(library)
    configure_provider(session)

    response = library.post(f"/api/assets/{created['id']}/generate-all")

    assert response.status_code == 202
    assert response.json()["data"]["action"] == KIND_GENERATE_ALL


def test_without_a_provider_the_button_is_told_why(library, session):
    created = _upload_image(library)

    response = library.post(f"/api/assets/{created['id']}/generate-all")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "provider_unavailable"


def test_a_second_run_while_one_is_going_is_refused(library, session):
    created = _upload_image(library)
    configure_provider(session)
    library.post(f"/api/assets/{created['id']}/generate-all")

    response = library.post(f"/api/assets/{created['id']}/generate-all")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "already_running"


def test_someone_elses_asset_is_a_404(library, session):
    configure_provider(session)
    session.add(
        Asset(
            id="theirs",
            user_id="a-different-user",
            name="Theirs",
            asset_type="image",
            storage_key="k",
        )
    )
    session.commit()

    assert library.post("/api/assets/theirs/generate-all").status_code == 404


def test_authentication_is_required(client):
    assert client.post("/api/assets/anything/generate-all").status_code == 401


# ─── through the worker ──────────────────────────────────────────────────────


def test_the_worker_runs_it_end_to_end(library, session, monkeypatch, upstream):
    created = _upload_image(library)
    add_transcript(session, created["id"], ["Giordano on neuroweapons."])
    configure_provider(session)

    job = library.post(f"/api/assets/{created['id']}/generate-all").json()["data"]

    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job["id"])

    session.expire_all()
    assert session.get(EnrichmentJob, job["id"]).status == "done"
    asset = session.get(Asset, created["id"])
    assert asset.summary == SUMMARY
    assert asset.description == DESCRIPTION
