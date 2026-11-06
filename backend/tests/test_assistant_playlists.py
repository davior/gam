"""The assistant building a playlist: "a playlist of people talking about the river".

As with everything the assistant does, the playlist is proposed and made only when the
person applies it, through the same service a person's own playlist goes through. The
moments are ranges in the playlist; no clips are made. The suite is faked as in
test_assistant.py, and the video is the real two-second fixture.
"""

import pytest
from sqlmodel import select

from app.media_tools import ffmpeg_available
from app.models.asset import Asset
from app.models.playlist import Playlist
from tests.test_assistant import an_asset, new_session, reply, run
from tests.test_assistant import fake_suite_fixture as fake_suite  # noqa: F401 - the fixture
from tests.test_assistant_clips import a_video, made_clips, what_the_model_saw
from tests.test_clips_api import _upload_audio

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def playlist_call(**args):
    return reply("", ("propose_playlist", args))


@needs_ffmpeg
def test_a_playlist_of_moments_is_proposed_and_made_only_on_apply(library, session, fake_suite):
    video = a_video(library, session)
    audio = session.get(Asset, _upload_audio(library)["id"])
    fake_suite.script += [
        reply("", ("search_assets", {"query": "river"})),
        playlist_call(name="The river", description="Every mention of the river.", items=[
            {"asset_id": video.id, "start_seconds": 1, "end_seconds": 2, "label": "Rising",
             "reason": "Says the river is rising."},
            {"asset_id": audio.id},
        ]),
        reply("A playlist of two."),
    ]
    done = run(library, new_session(library), "Make a playlist of people talking about the river")

    (proposed,) = done["proposal"]["playlists"]
    assert proposed["playlist_id"] is None and proposed["name"] == "The river"
    assert [(i["asset_id"], i["in_point"], i["out_point"]) for i in proposed["items"]] == [
        (video.id, 1.0, 2.0), (audio.id, None, None)]
    assert proposed["items"][0]["reason"] == "Says the river is rising."
    assert done["outcome"] == "pending"
    assert session.exec(select(Playlist)).all() == []

    (result,) = library.post(f"/api/assistant/turns/{done['id']}/apply").json()["data"]
    assert result["playlist_ref"] == proposed["playlist_ref"] and result["skipped"] == []
    assert "playlist made" in result["applied"] and result["asset_id"] is None

    made = library.get(f"/api/playlists/{result['playlist_id']}").json()["data"]
    assert made["name"] == "The river" and made["description"] == "Every mention of the river."
    assert [(i["asset_id"], i["start"], i["label"], i["note"]) for i in made["items"]][0] == (
        video.id, 1.0, "Rising", "Says the river is rising.")
    # Ranges in the playlist, not clips in the library.
    assert made_clips(library, video) == []


@needs_ffmpeg
def test_more_calls_add_to_the_same_proposed_playlist_and_duplicates_are_dropped(
        library, session, fake_suite):
    video = a_video(library, session)
    moment = {"asset_id": video.id, "start_seconds": 0, "end_seconds": 1}
    fake_suite.script += [
        playlist_call(name="River", items=[moment]),
        playlist_call(name="river", items=[
            {**moment, "start_seconds": 0.1, "end_seconds": 1.1},
            {"asset_id": video.id, "start_seconds": 1, "end_seconds": 2}]),
        reply("Done."),
    ]
    done = run(library, new_session(library))
    (proposed,) = done["proposal"]["playlists"]
    assert [(i["in_point"], i["out_point"]) for i in proposed["items"]] == [(0.0, 1.0), (1.0, 2.0)]
    assert "already in this playlist" in what_the_model_saw(fake_suite, 2)


@needs_ffmpeg
def test_bad_items_are_reported_to_the_model_and_the_good_ones_kept(library, session, fake_suite):
    video = a_video(library, session)
    document = Asset(user_id="user-under-test", name="Notes", asset_type="document",
                     storage_key="x/notes.pdf")
    session.add(document)
    session.commit()
    fake_suite.script += [
        playlist_call(name="Mixed", items=[
            {"asset_id": video.id, "start_seconds": 0, "end_seconds": 99},
            {"asset_id": document.id},
            {"asset_id": "someone-elses"},
            {"asset_id": video.id, "start_seconds": "soon", "end_seconds": 1},
            {"asset_id": video.id, "start_seconds": "0:00.5", "end_seconds": "0:01.5"},
        ]),
        playlist_call(items=[{"asset_id": video.id}]),
        reply("Partly."),
    ]
    done = run(library, new_session(library))
    (proposed,) = done["proposal"]["playlists"]
    assert [(i["in_point"], i["out_point"]) for i in proposed["items"]] == [(0.5, 1.5)]
    seen = what_the_model_saw(fake_suite, 1)
    for sentence in ("item 1: The out point is past the end", "item 2: Notes is a document",
                     "item 3: No such asset", "item 4: give the times as numbers"):
        assert sentence in seen
    assert "Give a name for the new playlist" in what_the_model_saw(fake_suite, 2)


@needs_ffmpeg
def test_adding_to_an_existing_playlist(library, session, fake_suite):
    video = a_video(library, session)
    existing = library.post("/api/playlists", json={
        "name": "Mine", "items": [{"asset_id": video.id}]}).json()["data"]
    fake_suite.script += [
        reply("", ("list_playlists", {})),
        playlist_call(playlist_id=existing["id"], items=[
            {"asset_id": video.id, "start_seconds": 1, "end_seconds": 2}]),
        reply("Added one."),
    ]
    done = run(library, new_session(library), "Add the river bit to Mine")
    assert existing["id"] in what_the_model_saw(fake_suite, 1)
    (proposed,) = done["proposal"]["playlists"]
    assert proposed["playlist_id"] == existing["id"] and proposed["name"] == "Mine"

    (result,) = library.post(f"/api/assistant/turns/{done['id']}/apply").json()["data"]
    assert "items added" in result["applied"] and result["playlist_id"] == existing["id"]
    items = library.get(f"/api/playlists/{existing['id']}").json()["data"]["items"]
    assert [(i["in_point"], i["out_point"]) for i in items] == [(None, None), (1.0, 2.0)]


@needs_ffmpeg
def test_apply_skips_what_has_gone_and_respects_the_selection(library, session, fake_suite):
    video = a_video(library, session)
    image = an_asset(library, session)
    fake_suite.script += [
        reply("", ("propose_playlist", {"name": "One", "items": [
            {"asset_id": video.id}, {"asset_id": image.id, "label": "Picture"}]}),
                  ("propose_playlist", {"name": "Two", "items": [{"asset_id": video.id}]}),
                  ("propose_clip", {"asset_id": video.id, "start_seconds": 0, "end_seconds": 1})),
        reply("Two playlists and a clip."),
    ]
    done = run(library, new_session(library))
    one, two = done["proposal"]["playlists"]
    assert library.delete(f"/api/assets/{image.id}").status_code == 204

    results = library.post(f"/api/assistant/turns/{done['id']}/apply",
                           json={"playlist_refs": [one["playlist_ref"]]}).json()["data"]
    # Only the playlist asked for: not the other, and not the clip.
    (result,) = results
    assert result["playlist_ref"] == one["playlist_ref"]
    assert result["skipped"] == ["Picture: No such asset."]
    names = [p.name for p in session.exec(select(Playlist)).all()]
    assert names == ["One"] and made_clips(library, video) == []
