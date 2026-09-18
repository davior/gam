"""Describing an asset — what is in it, as opposed to what it is about.

The cases that carry their weight are the ones keeping this distinct from `summarize`.
Without them the two jobs read identical material and write the same paragraph into two
different columns, which is a lot of machinery for a duplicate.
"""

import json

import httpx
import pytest

from app.enrichment import describe, source
from app.jobs import enrichment as enrichment_jobs
from app.models.asset import Asset
from app.models.job import EnrichmentJob, KIND_DESCRIBE
from app.providers import _upstream
from app.providers.base import ProviderError
from app.services import assets as asset_service
from app.services import tags as tag_service

from tests.test_summarize import TEST_USER, _upload_image, add_transcript, configure_provider

DESCRIPTION = "James Giordano speaking to camera in a panelled studio, discussing DARPA."


@pytest.fixture(name="upstream")
def upstream_fixture(monkeypatch):
    state = {"calls": [], "text": DESCRIPTION}

    def fake_post_json(url, *, headers=None, json_body, timeout, label, **kwargs):
        state["calls"].append({"url": url, "body": json_body})
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": state["text"]}],
                "usage": {"input_tokens": 700, "output_tokens": 30},
            },
        )

    monkeypatch.setattr(_upstream, "post_json", fake_post_json)
    return state


def sent_content(state):
    return state["calls"][0]["body"]["messages"][0]["content"]


def sent_prompt(state) -> str:
    return "\n".join(b["text"] for b in sent_content(state) if b.get("type") == "text")


def sent_images(state):
    return [b for b in sent_content(state) if b.get("type") == "image"]


# ─── the thing that keeps it distinct from summarize ─────────────────────────


def test_a_transcribed_video_is_also_shown_its_frame(library, session, upstream):
    """Summarize gets the transcript alone. Describe gets the frame too, because the
    frame is the only source for anything visual and a transcript cannot say what
    something looks like. Without this the two jobs read identical bytes."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    add_transcript(session, asset.id, ["Giordano on neuroweapons."])
    configure_provider(session, supports_images=True)

    describe.run(session, asset, lambda *a, **k: None)

    assert len(sent_images(upstream)) == 1
    assert "Giordano on neuroweapons." in sent_prompt(upstream)
    assert "still frame" in sent_prompt(upstream)


def test_summarize_is_not_given_the_frame(library, session, upstream):
    """The other half of the same decision, asserted here so a change to `gather`'s
    default cannot quietly make them identical again."""
    from app.enrichment import summarize

    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    add_transcript(session, asset.id, ["Giordano on neuroweapons."])
    configure_provider(session, supports_images=True)

    summarize.run(session, asset, lambda *a, **k: None)

    assert sent_images(upstream) == []


def test_a_text_only_provider_still_gets_the_transcript(library, session, upstream):
    """The frame is an addition, not a requirement — describing from a transcript alone
    is worse but it is not nothing."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    add_transcript(session, asset.id, ["Giordano on neuroweapons."])
    configure_provider(session, supports_images=False)

    describe.run(session, asset, lambda *a, **k: None)

    assert sent_images(upstream) == []
    assert "Giordano on neuroweapons." in sent_prompt(upstream)


def test_an_existing_summary_is_context_not_material_to_repeat(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    asset.summary = "An interview about neuroweapons."
    session.commit()
    configure_provider(session)

    describe.run(session, asset, lambda *a, **k: None)

    prompt = sent_prompt(upstream)
    assert "An interview about neuroweapons." in prompt
    assert "do not repeat it" in prompt


def test_the_assets_own_tags_are_given_as_context(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    tag = tag_service.get_or_create(session, TEST_USER, "DARPA")
    tag_service.attach(session, asset.id, tag.id)
    configure_provider(session)

    describe.run(session, asset, lambda *a, **k: None)

    assert "DARPA" in sent_prompt(upstream)


# ─── the ordinary paths ──────────────────────────────────────────────────────


def test_an_image_is_described_from_the_image(library, session, upstream):
    created = _upload_image(library)
    configure_provider(session, supports_images=True)

    describe.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    assert len(sent_images(upstream)) == 1


def test_the_description_lands_on_the_asset(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session)

    describe.run(session, asset, lambda *a, **k: None)

    session.refresh(asset)
    assert asset.description == DESCRIPTION
    assert json.loads(asset.field_provenance)["description"] == "ai"


def test_a_description_someone_wrote_is_replaced_when_asked(library, session, upstream):
    """Pressing Describe is an explicit request for a fresh answer, so it always lands
    — even over a description a person typed themselves."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    asset_service.apply_metadata(session, asset, {"description": "Mine, thanks."})
    configure_provider(session)

    describe.run(session, asset, lambda *a, **k: None)

    session.refresh(asset)
    assert asset.description == DESCRIPTION
    assert json.loads(asset.field_provenance)["description"] == "ai"


def test_an_over_long_reply_is_trimmed(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session)
    upstream["text"] = "x" * (describe.MAX_DESCRIPTION_CHARS + 500)

    describe.run(session, asset, lambda *a, **k: None)

    session.refresh(asset)
    assert len(asset.description) <= describe.MAX_DESCRIPTION_CHARS


def test_an_image_with_a_text_only_provider_has_nothing_to_read(library, session):
    created = _upload_image(library)
    configure_provider(session, supports_images=False)

    with pytest.raises(source.NoSourceMaterial):
        describe.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)


def test_an_unconfigured_library_says_where_to_go(library, session):
    created = _upload_image(library)

    with pytest.raises(ProviderError, match="Settings"):
        describe.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)


# ─── the endpoint and the worker ─────────────────────────────────────────────


def test_describe_queues_a_job(library, session):
    created = _upload_image(library)
    configure_provider(session)

    response = library.post(f"/api/assets/{created['id']}/describe")

    assert response.status_code == 202
    assert response.json()["data"]["action"] == KIND_DESCRIBE


def test_describe_without_a_provider_says_why(library, session):
    created = _upload_image(library)

    response = library.post(f"/api/assets/{created['id']}/describe")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "provider_unavailable"


def test_a_second_run_while_one_is_going_is_refused(library, session):
    created = _upload_image(library)
    configure_provider(session)
    library.post(f"/api/assets/{created['id']}/describe")

    response = library.post(f"/api/assets/{created['id']}/describe")

    assert response.status_code == 409


def test_describe_requires_authentication(client):
    assert client.post("/api/assets/anything/describe").status_code == 401


def test_the_worker_runs_it_end_to_end(library, session, monkeypatch, upstream):
    """KIND_DESCRIBE has been declared since M4 with nothing behind it."""
    created = _upload_image(library)
    configure_provider(session)
    job = library.post(f"/api/assets/{created['id']}/describe").json()["data"]

    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job["id"])

    session.expire_all()
    assert session.get(EnrichmentJob, job["id"]).status == "done"
    assert session.get(Asset, created["id"]).description == DESCRIPTION
