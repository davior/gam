"""The assistant writing a note into Gecko Notes: proposed, then made on Apply.

"A summary of all the videos about dogs in Gecko Notes" ends in a note proposal. Nothing
reaches Notes until the person applies it, and then it is sent as them, with their own
token, which only the Apply request has. Notes itself is faked at the HTTP boundary,
answering as gecko-notes' `POST /api/notes/from-markdown` does.
"""

import json

import httpx
import pytest

from app import notes_client
from app.config import settings
from tests.test_assistant import an_asset, new_session, reply, run
from tests.test_assistant import fake_suite_fixture as fake_suite  # noqa: F401 - the fixture

NOTES = "https://notes.test"
TOKEN = "the-users-own-token"


class FakeNotes:
    def __init__(self):
        self.created = []
        self.status = 201
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused")
        assert request.url.path == "/api/notes/from-markdown" and request.method == "POST"
        body = json.loads(request.content)
        self.created.append({**body, "authorization": request.headers.get("authorization")})
        if self.status != 201:
            return httpx.Response(self.status, json={"detail": {"code": "x", "message": "No."}})
        return httpx.Response(201, json={"data": {"id": f"n{len(self.created)}",
                                                  "title": body["title"]}})


def test_notes_go_to_the_notes_app_not_to_where_people_sign_in(
        library, session, fake_suite, notes, monkeypatch):
    # Sign-in moved to Gecko Suite. When one setting meant both, a note went to the suite
    # and was answered 405 ("needs updating") by a Notes that was up to date all along.
    monkeypatch.setattr(settings, "suite_public_url", "https://suite.example")
    fake_suite.script += [propose_note(title="Dogs", markdown="text"), reply("Done.")]
    done = run(library, new_session(library))
    (result,) = apply(library, done["id"]).json()["data"]
    assert result["applied"] == ["note created"]
    assert result["note_url"].startswith(f"{NOTES}/notes/")


@pytest.fixture(name="notes")
def notes_fixture(monkeypatch):
    fake = FakeNotes()
    monkeypatch.setattr(settings, "notes_url", NOTES)
    monkeypatch.setattr(notes_client, "TRANSPORT", httpx.MockTransport(fake.handler))
    return fake


def apply(library, turn_id, **body):
    return library.post(f"/api/assistant/turns/{turn_id}/apply", json=body or None,
                        headers={"Authorization": f"Bearer {TOKEN}",
                                 "Origin": "https://gam.example"})


def propose_note(**args):
    return reply("", ("propose_note", args))


def test_a_note_is_proposed_and_written_to_notes_only_on_apply(library, session, fake_suite, notes):
    dog = an_asset(library, session, "A dog learns to sit.")
    markdown = f"# Dogs\n\nOne video:\n\n- [Puppy training](/a/{dog.id}): learning to sit."
    fake_suite.script += [
        reply("", ("search_assets", {"query": "dogs"})),
        propose_note(title="Dogs in the library", markdown=markdown),
        reply("A note for Gecko Notes, ready to apply."),
    ]
    done = run(library, new_session(library), "A summary of all the videos about dogs in Gecko Notes")

    (note,) = done["proposal"]["notes"]
    assert note["title"] == "Dogs in the library" and note["asset_ids"] == [dog.id]
    assert note["folder"] is None
    assert notes.created == []

    (result,) = apply(library, done["id"]).json()["data"]
    assert result["note_ref"] == note["note_ref"] and result["applied"] == ["note created"]
    assert result["note_url"] == f"{NOTES}/notes/n1"
    (sent,) = notes.created
    # As the person, filed as GAM's, with the asset link pointing back at GAM.
    assert sent["authorization"] == f"Bearer {TOKEN}"
    assert sent["source_app"] == "gam" and sent["folder_name"] is None
    assert f"](https://gam.example/a/{dog.id})" in sent["markdown"]


def test_links_to_ids_that_are_not_the_callers_assets_are_refused_to_the_model(
        library, session, fake_suite, notes):
    fake_suite.script += [
        propose_note(title="Dogs", markdown="- [A guess](/a/not-a-real-id)"),
        propose_note(title="Dogs", markdown="", folder="Pets"),
        reply("Couldn't."),
    ]
    done = run(library, new_session(library))
    assert done["proposal"] is None
    seen = json.dumps(fake_suite.chats[1]["messages"])
    assert "not-a-real-id" in seen and "not assets in the library" in seen
    assert "Give the note's text" in json.dumps(fake_suite.chats[2]["messages"])


def test_the_same_title_again_rewrites_that_note_and_a_named_folder_is_kept(
        library, session, fake_suite, notes):
    fake_suite.script += [
        reply("", ("propose_note", {"title": "Dogs", "markdown": "first"}),
              ("propose_note", {"title": "dogs", "markdown": "second", "folder": "Pets"})),
        reply("One note."),
    ]
    done = run(library, new_session(library))
    (note,) = done["proposal"]["notes"]
    assert (note["markdown"], note["folder"]) == ("second", "Pets")
    apply(library, done["id"])
    assert notes.created[0]["folder_name"] == "Pets"


@pytest.mark.parametrize("trouble, sentence", [
    ("down", "can't be reached"),
    (401, "JWT_SECRET_KEY"),
    (404, f"Gecko Notes at {NOTES} answered 404"),
    # What a Notes from before the endpoint answers: it has /api/notes/{id}, but no POST.
    (405, f"Gecko Notes at {NOTES} answered 405"),
])
def test_notes_failing_is_a_sentence_and_the_rest_still_applies(
        library, session, fake_suite, notes, trouble, sentence):
    asset = an_asset(library, session)
    if trouble == "down":
        notes.down = True
    else:
        notes.status = trouble
    fake_suite.script += [
        reply("", ("propose_note", {"title": "Dogs", "markdown": "text"}),
              ("propose_changes", {"asset_id": asset.id, "name": "Renamed"})),
        reply("Both."),
    ]
    done = run(library, new_session(library))
    results = apply(library, done["id"]).json()["data"]
    by_kind = {("note" if r.get("note_ref") else "item"): r for r in results}
    assert by_kind["note"]["applied"] == [] and sentence in by_kind["note"]["skipped"][0]
    if trouble in (404, 405):
        # Both causes named, since an old Notes and a wrong address look alike from here.
        assert "NOTES_URL" in by_kind["note"]["skipped"][0]
    assert by_kind["item"]["applied"] == ["name"]


def test_only_the_notes_asked_for_are_written(library, session, fake_suite, notes):
    fake_suite.script += [
        reply("", ("propose_note", {"title": "One", "markdown": "a"}),
              ("propose_note", {"title": "Two", "markdown": "b"})),
        reply("Two notes."),
    ]
    done = run(library, new_session(library))
    one, _two = done["proposal"]["notes"]
    apply(library, done["id"], note_refs=[one["note_ref"]])
    assert [n["title"] for n in notes.created] == ["One"]


def test_without_a_token_the_note_is_not_written_and_says_why(library, session, fake_suite, notes):
    fake_suite.script += [propose_note(title="Dogs", markdown="text"), reply("Done.")]
    done = run(library, new_session(library))
    (result,) = library.post(f"/api/assistant/turns/{done['id']}/apply").json()["data"]
    assert "Sign in through Gecko Notes" in result["skipped"][0]
    assert notes.created == []
