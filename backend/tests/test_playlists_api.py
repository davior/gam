"""Playlists over HTTP: building one, editing its ranges, and what deleting an asset does.

The media are the real fixtures, so a range is checked against a real measured duration.
"""

import pytest
from sqlmodel import select

from app.media_tools import ffmpeg_available
from app.models.asset import Asset
from app.models.playlist import PlaylistItem
from tests.test_clips_api import _clip, _upload_audio, _upload_image, _upload_video

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def a_playlist(client, name="Evening", items=()):
    response = client.post("/api/playlists", json={"name": name, "items": list(items)})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def a_document(session, user_id="user-under-test"):
    row = Asset(user_id=user_id, name="Notes", asset_type="document", storage_key="x/notes.pdf")
    session.add(row)
    session.commit()
    return row


@needs_ffmpeg
def test_a_playlist_holds_video_audio_and_images_in_order(library):
    video, audio, image = _upload_video(library), _upload_audio(library), _upload_image(library)
    made = a_playlist(library, items=[
        {"asset_id": video["id"], "in_point": 0.5, "out_point": 1.5, "label": "The bit"},
        {"asset_id": audio["id"]},
        {"asset_id": image["id"], "image_seconds": 3},
    ])
    assert [i["asset_id"] for i in made["items"]] == [video["id"], audio["id"], image["id"]]
    first, second, third = made["items"]
    assert (first["start"], first["end"], first["label"]) == (0.5, 1.5, "The bit")
    # The whole asset: nothing stored, the asset's own length resolved.
    assert first["file_url"].startswith("/media/") and not first["missing"]
    assert second["in_point"] is None and second["start"] == 0
    assert second["end"] == pytest.approx(second["asset_duration_seconds"])
    assert (third["start"], third["end"]) == (0, 3)
    assert made["item_count"] == 3

    listed = library.get("/api/playlists").json()
    assert listed["total"] == 1 and listed["data"][0]["item_count"] == 3
    assert listed["data"][0]["total_seconds"] == pytest.approx(1 + second["end"] + 3, abs=0.001)


@needs_ffmpeg
def test_a_clip_is_stored_as_its_original_and_window(library):
    video = _upload_video(library)
    clip = _clip(library, video["id"], 0.25, 1.25)
    item = a_playlist(library, items=[{"asset_id": clip["id"]}])["items"][0]
    # So Video Creator always cuts from a real file, and the range is editable.
    assert item["asset_id"] == video["id"]
    assert (item["in_point"], item["out_point"]) == (0.25, 1.25)

    refused = library.post("/api/playlists", json={
        "name": "x", "items": [{"asset_id": clip["id"], "in_point": 0, "out_point": 0.5}]})
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == "bad_range"


@needs_ffmpeg
def test_bad_items_add_nothing(library, session):
    video = _upload_video(library)
    playlist = a_playlist(library, items=[{"asset_id": video["id"]}])
    cases = [
        ({"asset_id": a_document(session).id}, "not_playable"),
        ({"asset_id": video["id"], "in_point": 1, "out_point": 99}, "bad_range"),
        ({"asset_id": video["id"], "in_point": 1}, "bad_range"),
        ({"asset_id": a_document(session, user_id="someone-else").id}, "not_found"),
        ({"asset_id": video["id"], "image_seconds": 4}, "bad_request"),
    ]
    for bad, code in cases:
        response = library.post(f"/api/playlists/{playlist['id']}/items",
                                json={"items": [{"asset_id": video["id"]}, bad]})
        assert response.status_code == 400, bad
        assert response.json()["detail"]["code"] == code
    assert library.get(f"/api/playlists/{playlist['id']}").json()["data"]["item_count"] == 1


@needs_ffmpeg
def test_ranges_are_editable_and_can_go_back_to_the_whole_asset(library):
    video = _upload_video(library)
    playlist = a_playlist(library, items=[{"asset_id": video["id"]}])
    item_id = playlist["items"][0]["id"]
    url = f"/api/playlists/{playlist['id']}/items/{item_id}"

    edited = library.patch(url, json={"in_point": 0.2, "out_point": 0.9}).json()["data"]["items"][0]
    assert (edited["start"], edited["end"]) == (0.2, 0.9)
    # A label alone leaves the range alone.
    labelled = library.patch(url, json={"label": "Opening"}).json()["data"]["items"][0]
    assert (labelled["in_point"], labelled["label"]) == (0.2, "Opening")

    bad = library.patch(url, json={"in_point": 1.5, "out_point": 1.0})
    assert bad.status_code == 400
    assert library.get(f"/api/playlists/{playlist['id']}").json()["data"]["items"][0]["in_point"] == 0.2

    whole = library.patch(url, json={"in_point": None, "out_point": None}).json()["data"]["items"][0]
    assert whole["in_point"] is None and whole["start"] == 0


@needs_ffmpeg
def test_items_are_added_at_a_position_reordered_and_removed(library):
    video = _upload_video(library)
    playlist = a_playlist(library, items=[
        {"asset_id": video["id"], "label": "a"}, {"asset_id": video["id"], "label": "b"}])
    pid = playlist["id"]
    added = library.post(f"/api/playlists/{pid}/items",
                         json={"items": [{"asset_id": video["id"], "label": "c"}], "position": 1})
    labels = [i["label"] for i in added.json()["data"]["items"]]
    assert labels == ["a", "c", "b"]

    ids = {i["label"]: i["id"] for i in added.json()["data"]["items"]}
    reordered = library.put(f"/api/playlists/{pid}/order",
                            json={"item_ids": [ids["b"], ids["a"], ids["c"]]}).json()["data"]
    assert [i["label"] for i in reordered["items"]] == ["b", "a", "c"]

    stale = library.put(f"/api/playlists/{pid}/order", json={"item_ids": [ids["a"], ids["b"]]})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_order"

    after = library.delete(f"/api/playlists/{pid}/items/{ids['a']}").json()["data"]
    assert [(i["label"], i["position"]) for i in after["items"]] == [("b", 0), ("c", 1)]


@needs_ffmpeg
def test_rename_and_delete(library, session):
    video = _upload_video(library)
    pid = a_playlist(library, items=[{"asset_id": video["id"]}])["id"]
    renamed = library.patch(f"/api/playlists/{pid}", json={"name": "Crisis", "description": "2008"})
    assert renamed.json()["data"]["name"] == "Crisis"
    assert renamed.json()["data"]["description"] == "2008"
    assert library.patch(f"/api/playlists/{pid}", json={"name": "  "}).status_code == 400

    assert library.delete(f"/api/playlists/{pid}").status_code == 204
    assert library.get(f"/api/playlists/{pid}").status_code == 404
    assert not session.exec(select(PlaylistItem)).all()


@needs_ffmpeg
def test_deleting_an_asset_takes_its_items_and_leaves_the_rest(library):
    video, audio = _upload_video(library), _upload_audio(library)
    pid = a_playlist(library, items=[{"asset_id": video["id"]}, {"asset_id": audio["id"]}])["id"]
    assert library.delete(f"/api/assets/{video['id']}").status_code == 204
    left = library.get(f"/api/playlists/{pid}").json()["data"]["items"]
    assert [i["asset_id"] for i in left] == [audio["id"]]


def test_someone_elses_playlist_is_missing(library, session):
    from app.services import playlists

    theirs = playlists.create(session, "someone-else", "Theirs")
    assert library.get(f"/api/playlists/{theirs.id}").status_code == 404
    assert library.delete(f"/api/playlists/{theirs.id}").status_code == 404
    assert library.get("/api/playlists").json()["total"] == 0
    assert library.post("/api/playlists", json={"name": ""}).status_code == 422
