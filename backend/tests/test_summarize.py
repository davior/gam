"""The summarize job — the first thing in M6 that writes something a person reads.

Two behaviours matter more than the rest and have most of the cases below: what the job
chooses to read (a transcribed video is summarised from its transcript, never from one
frame of it), and what it refuses to overwrite (a summary somebody typed).
"""

import json
from pathlib import Path

import httpx
import pytest
from sqlmodel import select

from app.auth import encrypt_api_key
from app.enrichment import source, summarize
from app.jobs import enrichment as enrichment_jobs
from app.models.asset import Asset
from app.models.job import EnrichmentJob, KIND_SUMMARIZE
from app.models.provider import AIProvider
from app.models.transcript import TranscriptSegment
from app.providers import _upstream
from app.providers.base import ProviderError
from app.services import assets as asset_service

FIXTURES = Path(__file__).parent / "fixtures"
TEST_USER = "user-under-test"

SUMMARY = "A long interview about neuroweapons, recorded in a studio."


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


def configure_provider(session, *, supports_images=True, provider_type="anthropic"):
    row = AIProvider(
        user_id=TEST_USER,
        name="Claude",
        provider_type=provider_type,
        model="claude-sonnet-4-20250514",
        api_key=encrypt_api_key("sk-test"),
        supports_images=supports_images,
        is_active=True,
    )
    session.add(row)
    session.commit()
    return row


def add_transcript(session, asset_id, lines):
    for idx, text in enumerate(lines):
        session.add(
            TranscriptSegment(
                asset_id=asset_id,
                user_id=TEST_USER,
                idx=idx,
                text=text,
                start_time=float(idx),
                end_time=float(idx + 1),
            )
        )
    session.commit()


@pytest.fixture(name="upstream")
def upstream_fixture(monkeypatch):
    """Answer every completion with a canned summary, and record what was asked."""
    state = {"calls": [], "text": SUMMARY}

    def fake_post_json(url, *, headers=None, json_body, timeout, label, **kwargs):
        state["calls"].append({"url": url, "body": json_body})
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": state["text"]}],
                "usage": {"input_tokens": 900, "output_tokens": 40},
            },
        )

    monkeypatch.setattr(_upstream, "post_json", fake_post_json)
    return state


def sent_prompt(state) -> str:
    content = state["calls"][0]["body"]["messages"][0]["content"]
    return "\n".join(b["text"] for b in content if b.get("type") == "text")


def sent_images(state) -> list:
    content = state["calls"][0]["body"]["messages"][0]["content"]
    return [b for b in content if b.get("type") == "image"]


# ─── what it reads ───────────────────────────────────────────────────────────


def test_a_transcript_beats_the_poster_frame(library, session, upstream):
    """The rule the whole source step exists for. One frame of a two-hour interview
    shows a person sitting down; the transcript says what was discussed."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    asset.thumb_key = "does-not-matter"
    session.commit()
    add_transcript(session, asset.id, ["Giordano on neuroweapons.", "And on nanotech."])
    configure_provider(session)

    summarize.run(session, asset, lambda *a, **k: None)

    assert "Giordano on neuroweapons." in sent_prompt(upstream)
    assert sent_images(upstream) == []


def test_transcript_segments_go_in_order(session, library):
    """Read back by idx, not insertion order: a summary built from shuffled dialogue is
    wrong in a way that is hard to notice."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    add_transcript(session, asset.id, ["First.", "Second.", "Third."])

    assert source.transcript_text(session, asset) == "First.\nSecond.\nThird."


def test_an_image_is_sent_to_a_provider_that_can_see(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session, supports_images=True)

    summarize.run(session, asset, lambda *a, **k: None)

    assert len(sent_images(upstream)) == 1


def test_an_image_asset_with_a_text_only_provider_has_nothing_to_read(library, session):
    """Better than sending it anyway and getting a deserialization complaint back."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session, supports_images=False)

    with pytest.raises(source.NoSourceMaterial, match="Transcribe this asset first"):
        summarize.run(session, asset, lambda *a, **k: None)


def test_a_document_says_the_gap_out_loud(library, session):
    """`extract_text` is specified in plan-of-attack and does not exist, so this reads
    as a known gap rather than a file that mysteriously cannot be enriched."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    asset.asset_type = "document"
    asset.thumb_key = None
    session.commit()
    configure_provider(session, supports_images=False)

    with pytest.raises(source.NoSourceMaterial, match="documents is not built yet"):
        summarize.run(session, asset, lambda *a, **k: None)


def test_a_very_long_transcript_is_cut_and_says_so(library, session, upstream):
    """Otherwise the model writes 'the talk concludes by…' about material it never saw."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    add_transcript(session, asset.id, ["word " * 200 for _ in range(200)])
    configure_provider(session)

    summarize.run(session, asset, lambda *a, **k: None)

    prompt = sent_prompt(upstream)
    assert "opening portion only" in prompt
    assert len(prompt) < source.MAX_TRANSCRIPT_CHARS + 5000


def test_the_owners_own_note_is_given_as_context(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    asset.description = "Shot on the roof in Lisbon."
    session.commit()
    configure_provider(session)

    summarize.run(session, asset, lambda *a, **k: None)

    assert "Shot on the roof in Lisbon." in sent_prompt(upstream)


# ─── what it writes ──────────────────────────────────────────────────────────


def test_the_summary_lands_on_the_asset(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session)

    summarize.run(session, asset, lambda *a, **k: None)

    session.refresh(asset)
    assert asset.summary == SUMMARY
    assert json.loads(asset.field_provenance)["summary"] == "ai"


def test_it_refuses_to_overwrite_a_summary_someone_wrote(library, session, upstream):
    """FR 8.1.3. `field_provenance` was written from M1 and read by nothing until now."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    asset_service.apply_metadata(session, asset, {"summary": "Mine, thanks."})
    configure_provider(session)

    detail = summarize.run(session, asset, lambda *a, **k: None)

    session.refresh(asset)
    assert asset.summary == "Mine, thanks."
    # Reported rather than silently doing nothing and claiming success.
    assert "you wrote this" in detail.lower()


def test_a_previous_ai_summary_is_replaced(library, session, upstream):
    """Only a human edit is protected; re-running should be able to improve its own work."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session)
    summarize.run(session, asset, lambda *a, **k: None)

    upstream["text"] = "A better summary."
    summarize.run(session, asset, lambda *a, **k: None)

    session.refresh(asset)
    assert asset.summary == "A better summary."


def test_an_over_long_reply_is_trimmed(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session)
    upstream["text"] = "x" * (summarize.MAX_SUMMARY_CHARS + 500)

    summarize.run(session, asset, lambda *a, **k: None)

    session.refresh(asset)
    assert len(asset.summary) <= summarize.MAX_SUMMARY_CHARS


def test_an_unconfigured_library_says_where_to_go(library, session):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])

    with pytest.raises(ProviderError, match="Settings"):
        summarize.run(session, asset, lambda *a, **k: None)


# ─── apply_ai_metadata, directly ─────────────────────────────────────────────


def test_ai_metadata_writes_an_untouched_field(library, session):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])

    written = asset_service.apply_ai_metadata(session, asset, {"summary": "auto"})

    assert written == ["summary"]
    assert json.loads(asset.field_provenance) == {"summary": "ai"}


def test_ai_metadata_skips_only_the_human_field(library, session):
    """A mixed write should land the parts it is allowed to, not abort wholesale."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    asset_service.apply_metadata(session, asset, {"description": "Mine."})

    written = asset_service.apply_ai_metadata(
        session, asset, {"description": "theirs", "summary": "auto"}
    )

    assert written == ["summary"]
    session.refresh(asset)
    assert asset.description == "Mine."
    assert asset.summary == "auto"


def test_ai_metadata_with_nothing_allowed_writes_nothing(library, session):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    asset_service.apply_metadata(session, asset, {"summary": "Mine."})
    before = asset.metadata_modified_date

    assert asset_service.apply_ai_metadata(session, asset, {"summary": "auto"}) == []
    session.refresh(asset)
    assert asset.metadata_modified_date == before


# ─── the endpoint ────────────────────────────────────────────────────────────


def test_summarize_queues_a_job(library, session):
    created = _upload_image(library)
    configure_provider(session)

    response = library.post(f"/api/assets/{created['id']}/summarize")

    assert response.status_code == 202
    assert response.json()["data"]["action"] == KIND_SUMMARIZE


def test_without_a_provider_the_button_is_told_why(library, session):
    """The code is what the frontend branches on to offer a link to Settings, the same
    shape the embed button already uses."""
    created = _upload_image(library)

    response = library.post(f"/api/assets/{created['id']}/summarize")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "provider_unavailable"


def test_a_second_run_while_one_is_going_is_refused(library, session):
    """Two runs would bill twice for the same transcript and race to write one field."""
    created = _upload_image(library)
    configure_provider(session)
    library.post(f"/api/assets/{created['id']}/summarize")

    response = library.post(f"/api/assets/{created['id']}/summarize")

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

    assert library.post("/api/assets/theirs/summarize").status_code == 404


def test_authentication_is_required(client):
    assert client.post("/api/assets/anything/summarize").status_code == 401


# ─── through the worker ──────────────────────────────────────────────────────


def test_the_worker_runs_it_end_to_end(library, session, monkeypatch, upstream):
    """The dispatch branch, not just the module — KIND_SUMMARIZE has been declared
    since M4 with nothing behind it."""
    created = _upload_image(library)
    add_transcript(session, created["id"], ["Giordano on neuroweapons."])
    configure_provider(session)

    job = library.post(f"/api/assets/{created['id']}/summarize").json()["data"]

    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job["id"])

    session.expire_all()
    assert session.get(EnrichmentJob, job["id"]).status == "done"
    assert session.get(Asset, created["id"]).summary == SUMMARY


def test_a_provider_failure_ends_the_job_with_a_sentence(library, session, monkeypatch):
    """Not a traceback, and not a row left at "processing" for the stale sweeper."""
    created = _upload_image(library)
    configure_provider(session)

    def refuse(url, **kwargs):
        return httpx.Response(401, json={"error": {"message": "credit balance is too low"}})

    monkeypatch.setattr(_upstream, "post_json", refuse)
    job = library.post(f"/api/assets/{created['id']}/summarize").json()["data"]

    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job["id"])

    session.expire_all()
    row = session.get(EnrichmentJob, job["id"])
    assert row.status == "error"
    assert "credit balance is too low" in row.error_message


def test_an_asset_with_nothing_to_read_fails_readably(library, session, monkeypatch):
    created = _upload_image(library)
    configure_provider(session, supports_images=False)
    job = library.post(f"/api/assets/{created['id']}/summarize").json()["data"]

    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job["id"])

    session.expire_all()
    row = session.get(EnrichmentJob, job["id"])
    assert row.status == "error"
    assert "Transcribe this asset first" in row.error_message
