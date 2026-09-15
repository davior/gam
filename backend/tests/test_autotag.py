"""Proposing tags and a title, and applying neither until asked.

FR 9.1.4 is what most of this file is about. The cases that matter most are the ones
proving a suggestion is *not* a tag: it must not reach `tags_for`, must not reach the
keyword index, and must not come back after it has been declined.
"""

import json

import httpx
import pytest
from sqlmodel import select

from app.auth import encrypt_api_key
from app.enrichment import autotag
from app.jobs import enrichment as enrichment_jobs
from app.models.asset import Asset
from app.models.job import EnrichmentJob, KIND_AUTOTAG
from app.models.provider import AIProvider
from app.models.suggestion import STATUS_PENDING, STATUS_REJECTED, Suggestion
from app.models.tag import AssetTag, Tag
from app.models.transcript import TranscriptSegment
from app.providers import _upstream
from app.providers.base import ProviderError
from app.services import suggestions as suggestion_service
from app.services import tags as tag_service

from tests.test_summarize import _upload_image, configure_provider, add_transcript

TEST_USER = "user-under-test"

REPLY = {"title": "Giordano on Neuroweapons", "tags": ["James Giordano", "neuroweapons", "DARPA"]}


@pytest.fixture(name="upstream")
def upstream_fixture(monkeypatch):
    state = {"calls": [], "reply": json.dumps(REPLY)}

    def fake_post_json(url, *, headers=None, json_body, timeout, label, **kwargs):
        state["calls"].append({"url": url, "body": json_body})
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": state["reply"]}],
                "usage": {"input_tokens": 500, "output_tokens": 30},
            },
        )

    monkeypatch.setattr(_upstream, "post_json", fake_post_json)
    return state


def sent_prompt(state) -> str:
    content = state["calls"][0]["body"]["messages"][0]["content"]
    return "\n".join(b["text"] for b in content if b.get("type") == "text")


def pending(session, asset_id):
    return suggestion_service.pending_for(session, asset_id)


# ─── parsing what came back ──────────────────────────────────────────────────


def test_a_clean_json_reply():
    title, tags = autotag.parse_reply(json.dumps(REPLY))

    assert title == "Giordano on Neuroweapons"
    assert tags == ["James Giordano", "neuroweapons", "DARPA"]


def test_a_fenced_reply_still_parses():
    """Told "JSON only", a model will still sometimes wrap it. Cheaper to allow for than
    to fail a whole run over."""
    title, _ = autotag.parse_reply("```json\n" + json.dumps(REPLY) + "\n```")

    assert title == "Giordano on Neuroweapons"


def test_a_reply_with_a_preamble_still_parses():
    title, tags = autotag.parse_reply("Here you go:\n" + json.dumps(REPLY) + "\nHope that helps!")

    assert title == "Giordano on Neuroweapons"
    assert len(tags) == 3


def test_non_string_tags_are_dropped_rather_than_crashing():
    title, tags = autotag.parse_reply('{"title": "A", "tags": ["good", 7, null, "  ", "fine"]}')

    assert tags == ["good", "fine"]


@pytest.mark.parametrize(
    "reply", ["", "   ", "not json at all", "[1, 2, 3]", '{"title": "", "tags": []}']
)
def test_an_unusable_reply_is_an_error(reply):
    with pytest.raises(ProviderError):
        autotag.parse_reply(reply)


def test_a_title_alone_is_enough():
    title, tags = autotag.parse_reply('{"title": "Just this", "tags": []}')

    assert title == "Just this"
    assert tags == []


# ─── the prompt ──────────────────────────────────────────────────────────────


def test_the_library_vocabulary_is_offered(library, session, upstream):
    """Matching the words someone already uses matters more than precision — otherwise a
    curated library slowly grows 'US politics' beside 'U.S. politics'."""
    created = _upload_image(library)
    session.add(Tag(user_id=TEST_USER, name="Geopolitics"))
    session.commit()
    configure_provider(session)

    autotag.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    assert "Geopolitics" in sent_prompt(upstream)


def test_an_existing_summary_is_given_as_context(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    asset.summary = "An interview about neuroweapons."
    session.commit()
    configure_provider(session)

    autotag.run(session, asset, lambda *a, **k: None)

    assert "An interview about neuroweapons." in sent_prompt(upstream)


def test_it_reads_the_transcript_like_summarize_does(library, session, upstream):
    created = _upload_image(library)
    add_transcript(session, created["id"], ["Giordano on neuroweapons."])
    configure_provider(session)

    autotag.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    assert "Giordano on neuroweapons." in sent_prompt(upstream)


# ─── nothing is applied ──────────────────────────────────────────────────────


def test_a_run_creates_suggestions_and_no_tags(library, session, upstream):
    """The whole point of the milestone step."""
    created = _upload_image(library)
    configure_provider(session)

    autotag.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    assert len(pending(session, created["id"])) == 4  # three tags and a title
    assert session.exec(select(Tag)).all() == []
    assert session.exec(select(AssetTag)).all() == []


def test_a_suggested_tag_is_not_in_the_keyword_index(library, session, upstream):
    """The reason suggestions are their own table. A `status` column on the join would
    have had to be excluded by `tags_text_for`, and the day it was not, an unapproved
    tag would be searchable."""
    created = _upload_image(library)
    configure_provider(session)

    autotag.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    assert tag_service.tags_text_for(session, created["id"]) == ""


def test_the_title_is_not_written(library, session, upstream):
    created = _upload_image(library)
    configure_provider(session)
    before = session.get(Asset, created["id"]).name

    autotag.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    session.expire_all()
    assert session.get(Asset, created["id"]).name == before


# ─── what gets proposed, and what does not ───────────────────────────────────


def test_a_tag_the_asset_already_has_is_not_proposed(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    tag = tag_service.get_or_create(session, TEST_USER, "DARPA")
    tag_service.attach(session, asset.id, tag.id)
    session.commit()
    configure_provider(session)

    autotag.run(session, asset, lambda *a, **k: None)

    values = {s.value for s in pending(session, asset.id)}
    assert "DARPA" not in values


def test_a_declined_tag_does_not_come_back(library, session, upstream):
    """Being asked twice about something you said no to is how this becomes noise."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session)
    autotag.run(session, asset, lambda *a, **k: None)

    darpa = next(s for s in pending(session, asset.id) if s.value == "DARPA")
    suggestion_service.reject(session, darpa)

    autotag.run(session, asset, lambda *a, **k: None)

    assert "DARPA" not in {s.value for s in pending(session, asset.id)}


def test_a_re_run_replaces_the_previous_pending_set(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session)
    autotag.run(session, asset, lambda *a, **k: None)

    upstream["reply"] = json.dumps({"title": "A better title", "tags": ["nanotech"]})
    autotag.run(session, asset, lambda *a, **k: None)

    values = {s.value for s in pending(session, asset.id)}
    assert values == {"A better title", "nanotech"}


def test_duplicates_within_one_reply_collapse(library, session, upstream):
    created = _upload_image(library)
    configure_provider(session)
    upstream["reply"] = json.dumps({"title": "", "tags": ["NATO", "nato", " NATO "]})

    autotag.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    assert len(pending(session, created["id"])) == 1


def test_too_many_tags_are_capped(library, session, upstream):
    """Past a handful the list stops being a decision and becomes a chore."""
    created = _upload_image(library)
    configure_provider(session)
    upstream["reply"] = json.dumps({"title": "", "tags": [f"tag{i}" for i in range(30)]})

    autotag.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    assert len(pending(session, created["id"])) == suggestion_service.MAX_TAGS


def test_a_title_matching_the_current_name_is_not_proposed(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session)
    upstream["reply"] = json.dumps({"title": asset.name, "tags": []})

    detail = autotag.run(session, asset, lambda *a, **k: None)

    assert pending(session, asset.id) == []
    assert "Nothing new" in detail


# ─── accepting and rejecting ─────────────────────────────────────────────────


def test_accepting_a_tag_attaches_it_for_real(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session)
    autotag.run(session, asset, lambda *a, **k: None)
    darpa = next(s for s in pending(session, asset.id) if s.value == "DARPA")

    suggestion_service.accept(session, asset, darpa)

    assert "DARPA" in tag_service.tags_text_for(session, asset.id)
    assert [t.name for t in tag_service.tags_for(session, asset.id)] == ["DARPA"]


def test_accepting_reuses_an_existing_tag_whatever_its_case(library, session, upstream):
    """Otherwise the library splits across two tags that look identical in the UI."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    existing = tag_service.get_or_create(session, TEST_USER, "darpa")
    configure_provider(session)
    autotag.run(session, asset, lambda *a, **k: None)
    darpa = next(s for s in pending(session, asset.id) if s.value == "DARPA")

    suggestion_service.accept(session, asset, darpa)

    assert len(session.exec(select(Tag)).all()) == 1
    assert tag_service.tags_for(session, asset.id)[0].id == existing.id


def test_accepting_a_title_renames_the_asset_as_a_human_edit(library, session, upstream):
    """The user read it and chose it, so a later run must not quietly replace it."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session)
    autotag.run(session, asset, lambda *a, **k: None)
    title = next(s for s in pending(session, asset.id) if s.kind == "title")

    suggestion_service.accept(session, asset, title)

    session.refresh(asset)
    assert asset.name == "Giordano on Neuroweapons"
    assert json.loads(asset.field_provenance)["name"] == "human"


def test_rejecting_leaves_the_library_untouched(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session)
    autotag.run(session, asset, lambda *a, **k: None)
    first = pending(session, asset.id)[0]

    suggestion_service.reject(session, first)

    assert session.exec(select(Tag)).all() == []
    assert first.id not in {s.id for s in pending(session, asset.id)}


def test_a_resolved_suggestion_cannot_be_resolved_twice(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session)
    autotag.run(session, asset, lambda *a, **k: None)
    darpa = next(s for s in pending(session, asset.id) if s.value == "DARPA")
    suggestion_service.reject(session, darpa)

    suggestion_service.accept(session, asset, darpa)

    assert darpa.status == STATUS_REJECTED
    assert session.exec(select(Tag)).all() == []


# ─── the endpoints ───────────────────────────────────────────────────────────


def test_autotag_queues_a_job(library, session):
    created = _upload_image(library)
    configure_provider(session)

    response = library.post(f"/api/assets/{created['id']}/autotag")

    assert response.status_code == 202
    assert response.json()["data"]["action"] == KIND_AUTOTAG


def test_autotag_without_a_provider_says_why(library, session):
    created = _upload_image(library)

    response = library.post(f"/api/assets/{created['id']}/autotag")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "provider_unavailable"


def test_the_pending_list_is_served(library, session, upstream):
    created = _upload_image(library)
    configure_provider(session)
    autotag.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    body = library.get(f"/api/assets/{created['id']}/suggestions").json()

    assert body["total"] == 4
    assert {s["kind"] for s in body["data"]} == {"tag", "title"}


def test_accept_and_reject_over_http(library, session, upstream):
    created = _upload_image(library)
    configure_provider(session)
    autotag.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)
    rows = library.get(f"/api/assets/{created['id']}/suggestions").json()["data"]
    tag_row = next(r for r in rows if r["kind"] == "tag")
    other = next(r for r in rows if r["id"] != tag_row["id"])

    accepted = library.post(
        f"/api/assets/{created['id']}/suggestions/{tag_row['id']}/accept"
    )
    rejected = library.post(
        f"/api/assets/{created['id']}/suggestions/{other['id']}/reject"
    )

    assert accepted.json()["data"]["status"] == "accepted"
    assert rejected.json()["data"]["status"] == "rejected"
    assert library.get(f"/api/assets/{created['id']}/suggestions").json()["total"] == 2


def test_someone_elses_suggestion_is_a_404(library, session, upstream):
    created = _upload_image(library)
    configure_provider(session)
    session.add(
        Suggestion(
            id="theirs",
            user_id="a-different-user",
            asset_id=created["id"],
            kind="tag",
            value="Secret",
        )
    )
    session.commit()

    response = library.post(f"/api/assets/{created['id']}/suggestions/theirs/accept")

    assert response.status_code == 404


def test_suggestions_require_authentication(client):
    assert client.get("/api/assets/anything/suggestions").status_code == 401


# ─── deletion, and the worker ────────────────────────────────────────────────


def test_an_asset_with_suggestions_can_still_be_deleted(library, session, upstream):
    """`Suggestion.asset_id` is a foreign key, so without cleanup this 500s — the same
    way a tagged asset used to be undeletable."""
    created = _upload_image(library)
    configure_provider(session)
    autotag.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    assert library.delete(f"/api/assets/{created['id']}").status_code == 204

    session.expire_all()
    assert session.exec(select(Suggestion)).all() == []


def test_the_worker_runs_autotag_end_to_end(library, session, monkeypatch, upstream):
    created = _upload_image(library)
    add_transcript(session, created["id"], ["Giordano on neuroweapons."])
    configure_provider(session)
    job = library.post(f"/api/assets/{created['id']}/autotag").json()["data"]

    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job["id"])

    session.expire_all()
    row = session.get(EnrichmentJob, job["id"])
    assert row.status == "done"
    assert "4 suggestions" in row.detail
    assert len(pending(session, created["id"])) == 4
