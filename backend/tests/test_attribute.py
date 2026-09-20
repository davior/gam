"""Proposing where an asset came from, and writing none of it (M10).

The cases that matter most are the ones proving the grounding rule holds: a field the
model cannot point to must not reach a suggestion, and a suggestion must not reach the
asset until somebody accepts it. A fabricated citation is worse than a blank one, and
every test here is about the difference.
"""

import json

import httpx
import pytest
from sqlmodel import select

from app.enrichment import attribute
from app.models.asset import Asset
from app.models.job import EnrichmentJob, KIND_ATTRIBUTE
from app.models.suggestion import (
    KIND_ATTRIBUTION,
    STATUS_PENDING,
    STATUS_REJECTED,
    Suggestion,
)
from app.providers import _upstream
from app.providers.base import ProviderError
from app.services import suggestions as suggestion_service

from tests.test_summarize import _upload_image, configure_provider, add_transcript

TEST_USER = "user-under-test"

REPLY = {
    "fields": [
        {
            "field": "publisher",
            "value": "BBC Two",
            "evidence": "the lower third reads BBC TWO",
        },
        {
            "field": "source_title",
            "value": "Panorama",
            "evidence": "the presenter says 'welcome to Panorama'",
        },
    ]
}


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


# ─── the grounding rule ──────────────────────────────────────────────────────


def test_a_grounded_reply_parses():
    found = attribute.parse_reply(json.dumps(REPLY))

    assert [f["field"] for f in found] == ["publisher", "source_title"]
    assert found[0]["evidence"] == "the lower third reads BBC TWO"


def test_a_field_with_no_evidence_is_dropped():
    """The rule this whole job is shaped around. A model that returns a publisher with
    no evidence has guessed, whatever the prompt asked for."""
    found = attribute.parse_reply(
        json.dumps({"fields": [{"field": "publisher", "value": "BBC Two"}]})
    )
    assert found == []


def test_an_empty_evidence_string_is_also_ungrounded():
    found = attribute.parse_reply(
        json.dumps({"fields": [{"field": "publisher", "value": "BBC", "evidence": "   "}]})
    )
    assert found == []


def test_grounded_and_ungrounded_fields_in_one_reply_are_separated():
    found = attribute.parse_reply(
        json.dumps(
            {
                "fields": [
                    {"field": "publisher", "value": "BBC", "evidence": "the logo"},
                    {"field": "creator", "value": "Probably someone famous"},
                ]
            }
        )
    )
    assert [f["field"] for f in found] == ["publisher"]


def test_an_empty_field_list_is_a_valid_answer():
    """Most material does not say where it came from, and saying so is the point."""
    assert attribute.parse_reply('{"fields": []}') == []


def test_an_unknown_field_name_is_ignored():
    found = attribute.parse_reply(
        json.dumps({"fields": [{"field": "vibe", "value": "ominous", "evidence": "the music"}]})
    )
    assert found == []


def test_credit_line_cannot_be_proposed():
    """It is composed from the others; a proposed sentence would assert more than the
    component fields support."""
    found = attribute.parse_reply(
        json.dumps(
            {"fields": [{"field": "credit_line", "value": "Courtesy BBC", "evidence": "x"}]}
        )
    )
    assert found == []


def test_a_loose_date_is_normalised_to_the_stored_format():
    found = attribute.parse_reply(
        json.dumps(
            {
                "fields": [
                    {
                        "field": "published_date",
                        "value": "2019-03-15T00:00:00Z",
                        "evidence": "the title card",
                    }
                ]
            }
        )
    )
    assert found[0]["value"] == "2019-03-15"


def test_an_unreadable_date_is_dropped_rather_than_stored():
    """The column has a format and the API refuses anything else, so a suggestion the
    user could never accept is worse than no suggestion."""
    found = attribute.parse_reply(
        json.dumps(
            {
                "fields": [
                    {"field": "published_date", "value": "sometime in the 90s", "evidence": "x"}
                ]
            }
        )
    )
    assert found == []


def test_a_fenced_reply_still_parses():
    found = attribute.parse_reply("```json\n" + json.dumps(REPLY) + "\n```")
    assert len(found) == 2


def test_a_reply_with_a_preamble_still_parses():
    found = attribute.parse_reply("Here you go:\n" + json.dumps(REPLY) + "\nHope that helps!")
    assert len(found) == 2


def test_an_empty_reply_is_an_error():
    with pytest.raises(ProviderError):
        attribute.parse_reply("")


def test_unusable_json_is_an_error():
    with pytest.raises(ProviderError):
        attribute.parse_reply("I could not tell you.")


# ─── the prompt ──────────────────────────────────────────────────────────────


def test_the_prompt_forbids_guessing(library, session, upstream):
    configure_provider(session)
    asset = _upload_image(library)
    add_transcript(session, asset["id"], ["Welcome to Panorama."])

    attribute.run(session, session.get(Asset, asset["id"]), lambda *a, **k: None)

    system = upstream["calls"][0]["body"]["system"]
    assert "evidence" in system
    assert "Do not infer" in system


def test_already_recorded_fields_are_named_so_they_are_not_re_proposed(
    library, session, upstream
):
    configure_provider(session)
    asset = _upload_image(library)
    library.patch(f"/api/assets/{asset['id']}", json={"publisher": "BBC"})
    add_transcript(session, asset["id"], ["Welcome."])

    attribute.run(session, session.get(Asset, asset["id"]), lambda *a, **k: None)

    assert "Already recorded" in sent_prompt(upstream)


# ─── nothing is written ──────────────────────────────────────────────────────


def test_a_run_writes_no_attribution_onto_the_asset(library, session, upstream):
    """The whole shape of this job. A citation only lands when a person says so."""
    configure_provider(session)
    asset = _upload_image(library)
    add_transcript(session, asset["id"], ["Welcome to Panorama."])

    attribute.run(session, session.get(Asset, asset["id"]), lambda *a, **k: None)

    stored = session.get(Asset, asset["id"])
    assert stored.publisher is None
    assert stored.source_title is None
    assert len(pending(session, asset["id"])) == 2


def test_a_suggestion_carries_its_evidence(library, session, upstream):
    configure_provider(session)
    asset = _upload_image(library)
    add_transcript(session, asset["id"], ["Welcome."])

    attribute.run(session, session.get(Asset, asset["id"]), lambda *a, **k: None)

    rows = library.get(f"/api/assets/{asset['id']}/suggestions").json()["data"]
    publisher = next(r for r in rows if r["field"] == "publisher")
    assert publisher["proposed_value"] == "BBC Two"
    assert publisher["evidence"] == "the lower third reads BBC TWO"


def test_accepting_writes_the_field_as_human(library, session, upstream):
    configure_provider(session)
    asset = _upload_image(library)
    add_transcript(session, asset["id"], ["Welcome."])
    attribute.run(session, session.get(Asset, asset["id"]), lambda *a, **k: None)

    row = next(r for r in pending(session, asset["id"]) if "publisher" in r.value)
    library.post(f"/api/assets/{asset['id']}/suggestions/{row.id}/accept")

    refreshed = library.get(f"/api/assets/{asset['id']}").json()["data"]
    assert refreshed["publisher"] == "BBC Two"

    stored = session.get(Asset, asset["id"])
    # "human", because the user read the evidence and chose it — the same reasoning the
    # accepted title already follows.
    assert json.loads(stored.field_provenance)["publisher"] == "human"


def test_rejecting_one_leaves_the_other(library, session, upstream):
    configure_provider(session)
    asset = _upload_image(library)
    add_transcript(session, asset["id"], ["Welcome."])
    attribute.run(session, session.get(Asset, asset["id"]), lambda *a, **k: None)

    row = next(r for r in pending(session, asset["id"]) if "publisher" in r.value)
    library.post(f"/api/assets/{asset['id']}/suggestions/{row.id}/reject")

    remaining = pending(session, asset["id"])
    assert len(remaining) == 1
    assert "source_title" in remaining[0].value


def test_a_declined_field_is_not_proposed_again(library, session, upstream):
    """Being asked twice about something you said no to is how a suggestion feature
    becomes noise."""
    configure_provider(session)
    asset = _upload_image(library)
    add_transcript(session, asset["id"], ["Welcome."])
    attribute.run(session, session.get(Asset, asset["id"]), lambda *a, **k: None)

    row = next(r for r in pending(session, asset["id"]) if "publisher" in r.value)
    suggestion_service.reject(session, row)

    attribute.run(session, session.get(Asset, asset["id"]), lambda *a, **k: None)

    assert all("publisher" not in r.value for r in pending(session, asset["id"]))


def test_a_field_already_filled_in_is_not_proposed_over(library, session, upstream):
    """Accepting writes through the human path, so allowing a proposal over an answer
    would make "accept" a way to quietly overwrite one."""
    configure_provider(session)
    asset = _upload_image(library)
    library.patch(f"/api/assets/{asset['id']}", json={"publisher": "Channel 4"})
    add_transcript(session, asset["id"], ["Welcome."])

    attribute.run(session, session.get(Asset, asset["id"]), lambda *a, **k: None)

    assert all("publisher" not in r.value for r in pending(session, asset["id"]))


def test_attribution_suggestions_do_not_clear_tag_suggestions(library, session, upstream):
    """The two jobs share a table; clearing every pending row would throw away
    suggestions the user has not seen from a run this one knows nothing about."""
    configure_provider(session)
    asset = _upload_image(library)
    add_transcript(session, asset["id"], ["Welcome."])

    session.add(
        Suggestion(user_id=TEST_USER, asset_id=asset["id"], kind="tag", value="nato", model="m")
    )
    session.commit()

    attribute.run(session, session.get(Asset, asset["id"]), lambda *a, **k: None)

    kinds = {r.kind for r in pending(session, asset["id"])}
    assert kinds == {"tag", KIND_ATTRIBUTION}


def test_a_run_that_grounds_nothing_says_so(library, session, upstream):
    configure_provider(session)
    asset = _upload_image(library)
    add_transcript(session, asset["id"], ["Welcome."])
    upstream["reply"] = '{"fields": []}'

    detail = attribute.run(session, session.get(Asset, asset["id"]), lambda *a, **k: None)

    assert detail == "Nothing it could point to"
    assert pending(session, asset["id"]) == []


# ─── the endpoint ────────────────────────────────────────────────────────────


def test_the_endpoint_queues_a_job(library, session, upstream):
    configure_provider(session)
    asset = _upload_image(library)
    add_transcript(session, asset["id"], ["Welcome."])

    response = library.post(f"/api/assets/{asset['id']}/attribute")

    assert response.status_code == 202
    assert response.json()["data"]["action"] == KIND_ATTRIBUTE


def test_a_second_run_while_one_is_going_is_a_409(library, session, upstream):
    configure_provider(session)
    asset = _upload_image(library)
    add_transcript(session, asset["id"], ["Welcome."])

    library.post(f"/api/assets/{asset['id']}/attribute")
    assert library.post(f"/api/assets/{asset['id']}/attribute").status_code == 409


def test_without_a_provider_it_refuses_rather_than_queueing(library, session):
    asset = _upload_image(library)
    add_transcript(session, asset["id"], ["Welcome."])

    response = library.post(f"/api/assets/{asset['id']}/attribute")

    assert response.status_code == 400
    assert session.exec(select(EnrichmentJob)).all() == []


# ─── decoding ────────────────────────────────────────────────────────────────


def test_a_malformed_payload_is_skipped_rather_than_breaking_the_panel():
    row = Suggestion(
        user_id="u", asset_id="a", kind=KIND_ATTRIBUTION, value="not json", model="m"
    )
    assert suggestion_service.decode_attribution(row) == {}


def test_a_payload_naming_an_unknown_field_is_skipped():
    row = Suggestion(
        user_id="u",
        asset_id="a",
        kind=KIND_ATTRIBUTION,
        value=json.dumps({"field": "vibe", "value": "ominous"}),
        model="m",
    )
    assert suggestion_service.decode_attribution(row) == {}
