"""The assistant: a saved conversation, a turn run on the suite, a proposal a person applies.

The suite is faked at the HTTP boundary, as in test_suite_backend.py, so these check what
GAM sends (who it speaks as, which job, which tools), what it does with the model's
answers, and above all that nothing in the library changes until Apply.
"""

import json
from typing import Dict, List, Optional

import httpx
import pytest
from sqlmodel import select

from app import suite
from app.assistant import worker
from app.config import settings
from app.models.asset import Asset
from app.models.assistant import AssistantSession, AssistantTurn
from app.models.job import EnrichmentJob
from app.services import tags as tag_service

from tests.test_suite_backend import SECRET, model
from tests.test_summarize import TEST_USER, _upload_image, add_transcript


def reply(text="", *calls, stop=None, cost=0.01) -> dict:
    tool_calls = [{"id": f"c{i}", "name": name, "arguments": args, "parse_error": None}
                  for i, (name, args) in enumerate(calls)]
    return {
        "text": text, "tool_calls": tool_calls, "stop": stop or ("tool_use" if calls else "end"),
        "refusal_category": None,
        "message": {"role": "assistant", "text": text, "tool_calls": tool_calls,
                    "raw": {"protocol": "anthropic", "message": {"role": "assistant", "content": []}}},
        "usage": {"input_tokens": 500, "output_tokens": 50, "cache_read_tokens": 0,
                  "cache_write_tokens": 0},
        "model": "claude-sonnet-5-5", "model_ref": "m1", "provider": "Claude",
        "vendor": "anthropic", "task": None, "cost": cost, "currency": "USD",
        "cost_estimated": True, "usage_event_id": "u"}


class FakeSuite:
    def __init__(self):
        self.script: List[dict] = []
        self.chats: List[dict] = []
        self.headers: List[Dict[str, str]] = []
        self.default: Optional[dict] = model()
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused")
        self.headers.append(dict(request.headers))
        if request.url.path == "/api/v1/resolve":
            task = request.url.params["task"]
            if task == "embed.text":
                return httpx.Response(200, json={"data": {"model": None}})
            return httpx.Response(200, json={"data": {"model": self.default}})
        if request.url.path == "/api/v1/ai/chat":
            self.chats.append(json.loads(request.content))
            assert self.script, "the suite was asked more often than the script allows"
            return httpx.Response(200, json={"data": self.script.pop(0)})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


@pytest.fixture(name="fake_suite")
def fake_suite_fixture(monkeypatch, engine):
    fake = FakeSuite()
    monkeypatch.setattr(settings, "ai_backend", "suite")
    monkeypatch.setattr(settings, "suite_app_secret", SECRET)
    monkeypatch.setattr(suite, "TRANSPORT", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(worker.queue, "engine", engine)
    return fake


def new_session(client) -> str:
    res = client.post("/api/assistant/sessions")
    assert res.status_code == 201, res.text
    return res.json()["data"]["id"]


def ask(client, session_id, prompt="Tidy this up", **body) -> dict:
    res = client.post(f"/api/assistant/sessions/{session_id}/turns",
                      json={"prompt": prompt, **body})
    assert res.status_code == 202, res.text
    return res.json()["data"]


def run(client, session_id, prompt="Tidy this up", **body) -> dict:
    turn = ask(client, session_id, prompt, **body)
    worker._run(turn["id"])
    return client.get(f"/api/assistant/turns/{turn['id']}").json()["data"]


def an_asset(library, session, text="Giordano on neuroweapons."):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    add_transcript(session, asset.id, [text])
    return asset


# ─── status ──────────────────────────────────────────────────────────────────


def test_local_mode_says_the_assistant_needs_the_suite_and_refuses_turns(library):
    status = library.get("/api/assistant/status").json()["data"]
    assert status["available"] is False and "AI_BACKEND=suite" in status["message"]
    sid = new_session(library)
    res = library.post(f"/api/assistant/sessions/{sid}/turns", json={"prompt": "hi"})
    assert res.status_code == 409 and res.json()["detail"]["code"] == "assistant_needs_suite"
    # Saved conversations stay readable and deletable.
    assert library.get(f"/api/assistant/sessions/{sid}").status_code == 200
    assert library.delete(f"/api/assistant/sessions/{sid}").status_code == 204


def test_status_reports_the_routed_model(library, fake_suite):
    status = library.get("/api/assistant/status").json()["data"]
    assert status == {"available": True, "ready": True, "model": "Claude Sonnet 5.5",
                      "message": None}


def test_status_says_where_to_fix_an_unrouted_or_toolless_model(library, fake_suite):
    fake_suite.default = None
    status = library.get("/api/assistant/status").json()["data"]
    assert status["ready"] is False and "Gecko Suite (Routing)" in status["message"]
    fake_suite.default = model("DeepSeek Chat", "deepseek-chat", ["chat"])
    status = library.get("/api/assistant/status").json()["data"]
    assert status["ready"] is False and "can't use tools" in status["message"]


def test_a_suite_that_is_down_is_a_503_and_the_library_still_works(library, fake_suite):
    sid = new_session(library)
    fake_suite.down = True
    res = library.post(f"/api/assistant/sessions/{sid}/turns", json={"prompt": "hi"})
    assert res.status_code == 503 and res.json()["detail"]["code"] == "ai_unavailable"
    assert library.get("/api/assets").status_code == 200


# ─── a turn ──────────────────────────────────────────────────────────────────


def test_a_turn_searches_proposes_and_changes_nothing_until_applied(
        library, session, fake_suite):
    asset = an_asset(library, session)
    original = asset.name
    fake_suite.script += [
        reply("Looking.", ("search_assets", {"query": "sample"})),
        reply("", ("read_transcript", {"asset_id": asset.id})),
        reply("", ("propose_changes", {"asset_id": asset.id, "name": "Giordano interview",
                                       "description": "A studio interview.",
                                       "add_tags": ["Interview", "neuroweapons"]})),
        reply("I'd rename it and tag it."),
    ]
    sid = new_session(library)
    done = run(library, sid, "Name and tag the Giordano recording", focus_asset_id=asset.id)

    assert done["status"] == "done", done
    assert done["reply"] == "I'd rename it and tag it." and done["outcome"] == "pending"
    item = done["proposal"]["items"][0]
    assert item["asset_id"] == asset.id and item["name"] == "Giordano interview"
    assert item["add_tags"] == ["Interview", "neuroweapons"]
    assert item["before"]["name"] == original
    assert done["cost"] == pytest.approx(0.04)
    assert [s["kind"] for s in done["steps"]] == ["think", "search", "read", "propose"]

    # Called as GAM for the person who asked, as a conversation of its own.
    first = fake_suite.chats[0]
    assert (first["task"], first["app"]) == ("chat.assistant", "gam")
    assert first["subject_ref"] == f"gam:assistant:{sid}"
    assert {t["name"] for t in first["tools"]} >= {"search_assets", "propose_changes"}
    assert asset.id in first["system"][-1]  # the open asset is "this"
    caller = fake_suite.headers[-1]
    assert caller["x-gecko-app"] == "gam" and caller["x-on-behalf-of"] == TEST_USER
    assert "authorization" not in caller

    # The search result and the transcript went back to the model, with timestamps.
    seen = json.dumps(fake_suite.chats[2]["messages"])
    assert "Giordano on neuroweapons." in seen and "[0:00]" in seen

    # Nothing has been written.
    session.refresh(asset)
    assert asset.name == original and asset.description is None
    assert tag_service.tags_for(session, asset.id) == []


def test_apply_writes_through_the_services_and_only_once(library, session, fake_suite):
    asset = an_asset(library, session)
    existing = tag_service.get_or_create(session, TEST_USER, "draft")
    tag_service.attach(session, asset.id, existing.id)
    fake_suite.script += [
        reply("", ("propose_changes", {"asset_id": asset.id, "name": "Giordano interview",
                                       "summary": "Two sentences.", "add_tags": ["Interview"],
                                       "remove_tags": ["Draft"]})),
        reply("Done."),
    ]
    done = run(library, new_session(library))
    res = library.post(f"/api/assistant/turns/{done['id']}/apply")
    assert res.status_code == 200, res.text
    assert res.json()["data"][0]["applied"] == ["name", "summary", "tags"]

    session.refresh(asset)
    provenance = json.loads(asset.field_provenance)
    assert asset.name == "Giordano interview" and asset.summary == "Two sentences."
    # A name a person approved is theirs; a summary is the model's.
    assert provenance["name"] == "human" and provenance["summary"] == "ai"
    assert [t.name for t in tag_service.tags_for(session, asset.id)] == ["Interview"]
    # And the new tag is findable, so the index was refreshed.
    found = library.get("/api/search", params={"q": "Interview"}).json()
    assert asset.id in json.dumps(found)

    again = library.post(f"/api/assistant/turns/{done['id']}/apply")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "nothing_to_apply"
    assert library.get(f"/api/assistant/turns/{done['id']}").json()["data"]["outcome"] == "applied"


def test_apply_can_take_some_assets_and_leave_the_rest(library, session, fake_suite):
    first, second = an_asset(library, session), an_asset(library, session)
    fake_suite.script += [
        reply("", ("propose_changes", {"asset_id": first.id, "name": "One"}),
              ("propose_changes", {"asset_id": second.id, "name": "Two"})),
        reply("Two renames."),
    ]
    done = run(library, new_session(library))
    res = library.post(f"/api/assistant/turns/{done['id']}/apply", json={"asset_ids": [second.id]})
    assert [r["asset_id"] for r in res.json()["data"]] == [second.id]
    session.refresh(first)
    session.refresh(second)
    assert second.name == "Two" and first.name != "One"


def test_discard_leaves_the_library_alone_and_closes_the_proposal(library, session, fake_suite):
    asset = an_asset(library, session)
    fake_suite.script += [
        reply("", ("propose_changes", {"asset_id": asset.id, "name": "Nope"})), reply("Ok.")]
    done = run(library, new_session(library))
    assert library.post(f"/api/assistant/turns/{done['id']}/discard").status_code == 200
    assert library.post(f"/api/assistant/turns/{done['id']}/apply").status_code == 409
    session.refresh(asset)
    assert asset.name != "Nope"


def test_enrichment_proposals_queue_the_librarys_own_jobs_when_applied(
        library, session, fake_suite):
    asset = an_asset(library, session)
    fake_suite.script += [
        reply("", ("propose_enrichment", {"asset_id": asset.id, "actions": ["summarize"]})),
        reply("I'd summarise it."),
    ]
    done = run(library, new_session(library))
    assert len(session.exec(select(EnrichmentJob).where(EnrichmentJob.kind == "summarize")).all()) == 0
    res = library.post(f"/api/assistant/turns/{done['id']}/apply").json()["data"][0]
    assert res["applied"] == ["summarize"] and res["skipped"] == []
    assert len(session.exec(select(EnrichmentJob).where(EnrichmentJob.kind == "summarize", EnrichmentJob.asset_id == asset.id)).all()) == 1


# ─── what the model can't do ─────────────────────────────────────────────────


def test_an_asset_that_is_not_theirs_is_reported_missing_to_the_model(
        library, session, fake_suite):
    stranger = Asset(user_id="someone-else", name="Private", asset_type="image")
    session.add(stranger)
    session.commit()
    fake_suite.script += [
        reply("", ("get_asset", {"asset_id": stranger.id}),
              ("propose_changes", {"asset_id": stranger.id, "name": "Mine now"})),
        reply("I couldn't find it."),
    ]
    done = run(library, new_session(library))
    answered = [r for m in fake_suite.chats[1]["messages"] if m["role"] == "tool"
                for r in m["results"]]
    # Both calls were answered, and neither learned the asset exists.
    assert len(answered) == 2 and all(
        r["is_error"] and r["content"].startswith("No such asset") for r in answered)
    assert done["proposal"] is None and done["outcome"] == ""
    session.refresh(stranger)
    assert stranger.name == "Private"


def test_a_proposal_that_changes_nothing_is_not_a_proposal(library, session, fake_suite):
    asset = an_asset(library, session)
    fake_suite.script += [
        reply("", ("propose_changes", {"asset_id": asset.id, "name": asset.name})),
        reply("It's fine as it is."),
    ]
    done = run(library, new_session(library))
    assert done["proposal"] is None and done["reply"] == "It's fine as it is."


def test_the_suite_going_away_mid_turn_ends_that_turn_with_a_sentence(library, fake_suite):
    sid = new_session(library)
    turn = ask(library, sid)
    fake_suite.down = True
    worker._run(turn["id"])
    done = library.get(f"/api/assistant/turns/{turn['id']}").json()["data"]
    assert done["status"] == "error" and "can't be reached" in done["error"]
    messages = library.get(f"/api/assistant/sessions/{sid}").json()["data"]["messages"]
    assert messages[-1]["role"] == "assistant" and "can't be reached" in messages[-1]["text"]


def test_a_turn_stopped_before_it_started_never_calls_the_suite(library, fake_suite):
    turn = ask(library, new_session(library))
    assert library.post(f"/api/assistant/turns/{turn['id']}/cancel").status_code == 200
    worker._run(turn["id"])
    assert library.get(f"/api/assistant/turns/{turn['id']}").json()["data"]["status"] == "cancelled"
    assert fake_suite.chats == []


# ─── conversations ───────────────────────────────────────────────────────────


def test_a_conversation_is_saved_and_replayed_to_the_model(library, fake_suite):
    fake_suite.script += [reply("Hello."), reply("Again.")]
    sid = new_session(library)
    run(library, sid, "First question")
    run(library, sid, "Second question")
    second = fake_suite.chats[1]["messages"]
    assert [m["role"] for m in second][:3] == ["user", "assistant", "user"]
    detail = library.get(f"/api/assistant/sessions/{sid}").json()["data"]
    assert detail["title"] == "First question" and len(detail["turns"]) == 2
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    listed = library.get("/api/assistant/sessions").json()
    assert listed["total"] == 1 and listed["data"][0]["id"] == sid


def test_one_turn_at_a_time_in_a_conversation(library, fake_suite):
    sid = new_session(library)
    ask(library, sid)
    res = library.post(f"/api/assistant/sessions/{sid}/turns", json={"prompt": "again"})
    assert res.status_code == 409 and res.json()["detail"]["code"] == "turn_running"


def test_someone_elses_conversation_is_not_found(library, session, fake_suite):
    other = AssistantSession(id="theirs", user_id="someone-else")
    session.add(other)
    session.commit()
    assert library.get("/api/assistant/sessions/theirs").status_code == 404
    assert library.delete("/api/assistant/sessions/theirs").status_code == 404
    assert library.post("/api/assistant/sessions/theirs/turns",
                        json={"prompt": "hi"}).status_code == 404


def test_deleting_a_conversation_removes_its_turns(library, session, fake_suite):
    sid = new_session(library)
    ask(library, sid)
    assert library.delete(f"/api/assistant/sessions/{sid}").status_code == 204
    assert len(session.exec(select(AssistantTurn)).all()) == 0
    assert library.get(f"/api/assistant/sessions/{sid}").status_code == 404
