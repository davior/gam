"""The assistant finding a moment and proposing a clip or a cut of it.

Like the rest of the assistant, nothing here changes the library until the person applies the
proposal: a clip is made by `create_clip` (a window, no file) and a cut is queued as the same
job the "extract sub-video" button queues. The suite is faked at the HTTP boundary
(test_assistant.py); the video is the real two-second fixture, so the ranges are checked
against a real duration.
"""

import json

import pytest
from sqlmodel import select

from app.assistant import tools
from app.media_tools import ffmpeg_available
from app.models.asset import Asset
from app.models.job import KIND_EXTRACT_SUBVIDEO, EnrichmentJob
from tests.test_assistant import an_asset, new_session, reply, run
from tests.test_assistant import fake_suite_fixture as fake_suite  # noqa: F401 - the fixture
from tests.test_summarize import add_transcript
from tests.test_transcripts import _upload_video

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")

LINES = ["Hello and welcome.", "The river is rising."]


def a_video(library, session):
    """A real two-second video whose transcript has a segment per second."""
    asset = session.get(Asset, _upload_video(library)["id"])
    add_transcript(session, asset.id, LINES)
    return asset


def propose(kind, asset, start, end, **more):
    return reply("", (kind, {"asset_id": asset.id, "start_seconds": start, "end_seconds": end,
                             **more}))


def made_clips(library, asset):
    return library.get(f"/api/assets/{asset.id}/clips").json()["data"]


def what_the_model_saw(fake_suite, call):
    return json.dumps(fake_suite.chats[call]["messages"])


# ─── finding the moment ──────────────────────────────────────────────────────


@needs_ffmpeg
def test_the_transcript_can_be_read_as_a_window_with_each_segments_range(
        library, session, fake_suite):
    asset = a_video(library, session)
    fake_suite.script += [
        reply("", ("read_transcript", {"asset_id": asset.id, "start_seconds": 1, "end_seconds": 2})),
        reply("Found it."),
    ]
    run(library, new_session(library))
    seen = what_the_model_saw(fake_suite, 1)
    # The second segment, with where it starts and ends; the first lies before the window.
    assert "The river is rising." in seen and "Hello and welcome." not in seen
    assert "[0:01.0" in seen and "0:02.0]" in seen


def test_a_time_may_be_given_as_seconds_or_as_the_transcripts_own_clock():
    assert tools._seconds(62.4) == 62.4
    assert tools._seconds("1:02.4") == pytest.approx(62.4)
    assert tools._seconds("1:01:01") == 3661
    assert tools._seconds("12") == 12
    for nonsense in ("soon", "", None, True, float("nan")):
        assert tools._seconds(nonsense) is None


# ─── a clip ──────────────────────────────────────────────────────────────────


@needs_ffmpeg
def test_a_clip_is_proposed_from_the_transcript_and_made_only_on_apply(
        library, session, fake_suite):
    asset = a_video(library, session)
    fake_suite.script += [
        reply("", ("read_transcript", {"asset_id": asset.id})),
        propose("propose_clip", asset, 1.0, 2.0, name="The river",
                reason="Where the river is mentioned."),
        reply("One clip: the river line."),
    ]
    done = run(library, new_session(library), "Clip where the river is mentioned")

    (entry,) = done["proposal"]["clips"]
    assert (entry["kind"], entry["asset_id"], entry["in_point"], entry["out_point"]) == (
        "clip", asset.id, 1.0, 2.0)
    assert entry["name"] == "The river" and entry["reason"] == "Where the river is mentioned."
    assert done["outcome"] == "pending" and [s["kind"] for s in done["steps"]][-1] == "propose"
    # Proposed, not made.
    assert made_clips(library, asset) == []

    res = library.post(f"/api/assistant/turns/{done['id']}/apply")
    assert res.status_code == 200, res.text
    (result,) = res.json()["data"]
    assert result["clip_id"] == entry["clip_id"] and result["applied"] == ["clip made"]
    assert result["asset_id"] == asset.id and result["skipped"] == []

    clip = library.get(f"/api/assets/{result['created_asset_id']}").json()["data"]
    assert clip["name"] == "The river" and clip["parent_asset_id"] == asset.id
    assert (clip["in_point"], clip["out_point"]) == (1.0, 2.0)
    assert [c["id"] for c in made_clips(library, asset)] == [clip["id"]]

    # Applied once only: a second Apply would make a second clip.
    again = library.post(f"/api/assistant/turns/{done['id']}/apply")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "nothing_to_apply"


@needs_ffmpeg
def test_a_time_in_the_transcripts_own_clock_is_understood(library, session, fake_suite):
    asset = a_video(library, session)
    fake_suite.script += [propose("propose_clip", asset, "0:00.5", "0:01.5"), reply("Done.")]
    done = run(library, new_session(library))
    assert (done["proposal"]["clips"][0]["in_point"], done["proposal"]["clips"][0]["out_point"]) == (
        0.5, 1.5)


@needs_ffmpeg
def test_several_moments_are_several_clips_and_the_same_moment_twice_is_one(
        library, session, fake_suite):
    asset = a_video(library, session)
    fake_suite.script += [
        reply("", ("propose_clip", {"asset_id": asset.id, "start_seconds": 0, "end_seconds": 1}),
              ("propose_clip", {"asset_id": asset.id, "start_seconds": 1, "end_seconds": 2}),
              ("propose_clip", {"asset_id": asset.id, "start_seconds": 0.1, "end_seconds": 1.1})),
        reply("Two clips."),
    ]
    done = run(library, new_session(library))
    assert [(c["in_point"], c["out_point"]) for c in done["proposal"]["clips"]] == [
        (0.0, 1.0), (1.0, 2.0)]
    assert "Already proposed" in what_the_model_saw(fake_suite, 1)


@needs_ffmpeg
def test_a_range_that_cannot_be_clipped_is_refused_to_the_model_and_not_proposed(
        library, session, fake_suite):
    asset = a_video(library, session)
    image = an_asset(library, session)
    fake_suite.script += [
        reply("", ("propose_clip", {"asset_id": asset.id, "start_seconds": 1, "end_seconds": 1}),
              ("propose_clip", {"asset_id": asset.id, "start_seconds": 0, "end_seconds": 99}),
              ("propose_clip", {"asset_id": image.id, "start_seconds": 0, "end_seconds": 1}),
              ("propose_clip", {"asset_id": "someone-elses", "start_seconds": 0, "end_seconds": 1}),
              ("propose_clip", {"asset_id": asset.id, "start_seconds": "soon", "end_seconds": 1})),
        reply("I couldn't."),
    ]
    done = run(library, new_session(library))
    assert done["proposal"] is None and done["outcome"] == ""
    seen = what_the_model_saw(fake_suite, 1)
    for sentence in ("out point must be after the in point", "past the end of the asset",
                     "Only video and audio assets can be clipped", "No such asset",
                     "as numbers of seconds"):
        assert sentence in seen


@needs_ffmpeg
def test_a_clip_cannot_be_clipped_again(library, session, fake_suite):
    asset = a_video(library, session)
    clip_id = library.post(f"/api/assets/{asset.id}/clips",
                           json={"in_point": 0, "out_point": 1}).json()["data"]["id"]
    fake_suite.script += [
        reply("", ("propose_clip", {"asset_id": clip_id, "start_seconds": 0, "end_seconds": 1})),
        reply("No."),
    ]
    done = run(library, new_session(library))
    assert done["proposal"] is None
    assert "itself a clip" in what_the_model_saw(fake_suite, 1)


@needs_ffmpeg
def test_the_clips_already_made_can_be_listed(library, session, fake_suite):
    asset = a_video(library, session)
    made = library.post(f"/api/assets/{asset.id}/clips",
                        json={"in_point": 0, "out_point": 1, "name": "Welcome"}).json()["data"]
    fake_suite.script += [reply("", ("list_clips", {"asset_id": asset.id})), reply("One exists.")]
    run(library, new_session(library))
    seen = what_the_model_saw(fake_suite, 1)
    assert made["id"] in seen and "live clip" in seen and "Welcome" in seen


# ─── a cut ───────────────────────────────────────────────────────────────────


@needs_ffmpeg
def test_a_cut_is_queued_as_the_extract_job_when_applied_and_not_before(
        library, session, fake_suite):
    asset = a_video(library, session)
    fake_suite.script += [
        propose("propose_cut", asset, 0.5, 1.5, name="River excerpt"), reply("A cut.")]
    done = run(library, new_session(library), "Cut that into a new video")
    assert done["proposal"]["clips"][0]["kind"] == "cut"
    assert session.exec(select(EnrichmentJob)
                        .where(EnrichmentJob.kind == KIND_EXTRACT_SUBVIDEO)).all() == []

    res = library.post(f"/api/assistant/turns/{done['id']}/apply")
    (result,) = res.json()["data"]
    assert result["applied"] == ["cut queued"] and result["created_asset_id"] is None

    session.expire_all()
    (job,) = session.exec(select(EnrichmentJob)
                          .where(EnrichmentJob.kind == KIND_EXTRACT_SUBVIDEO)).all()
    assert job.asset_id == asset.id and job.status in ("queued", "processing")
    assert json.loads(job.payload) == {"mode": "extract", "in_point": 0.5, "out_point": 1.5,
                                       "name": "River excerpt"}

    # And the worker can make a video from exactly that: a real cut, a new standalone asset.
    from app.enrichment.extract_subvideo import run as run_extract

    _, created_id = run_extract(session, asset, job.payload, lambda *_a, **_k: None)
    cut = session.get(Asset, created_id)
    assert cut.name == "River excerpt" and cut.storage_key is not None
    assert cut.duration_seconds == pytest.approx(1.0, abs=0.5)
    session.refresh(asset)
    assert asset.storage_key is not None  # the original is untouched


@needs_ffmpeg
def test_several_cuts_of_one_video_are_all_queued(library, session, fake_suite):
    # The button's "one at a time per asset" guard would refuse the second; a request for
    # several moments of one video is the ordinary case here.
    asset = a_video(library, session)
    fake_suite.script += [
        reply("", ("propose_cut", {"asset_id": asset.id, "start_seconds": 0, "end_seconds": 1}),
              ("propose_cut", {"asset_id": asset.id, "start_seconds": 1, "end_seconds": 2})),
        reply("Two cuts."),
    ]
    done = run(library, new_session(library))
    results = library.post(f"/api/assistant/turns/{done['id']}/apply").json()["data"]
    assert [r["applied"] for r in results] == [["cut queued"], ["cut queued"]]
    session.expire_all()
    assert len(session.exec(select(EnrichmentJob)
                            .where(EnrichmentJob.kind == KIND_EXTRACT_SUBVIDEO)).all()) == 2


@needs_ffmpeg
def test_a_cut_already_waiting_in_the_queue_is_not_queued_twice(library, session, fake_suite):
    asset = a_video(library, session)
    for _ in range(2):
        fake_suite.script += [propose("propose_cut", asset, 0, 1), reply("A cut.")]
    first = run(library, new_session(library))
    second = run(library, new_session(library))
    library.post(f"/api/assistant/turns/{first['id']}/apply")
    (result,) = library.post(f"/api/assistant/turns/{second['id']}/apply").json()["data"]
    assert result["applied"] == [] and result["skipped"] == ["That cut is already being made."]


@needs_ffmpeg
def test_a_cut_is_not_queued_when_the_disk_is_full(library, session, fake_suite, monkeypatch):
    from app.storage import capacity

    asset = a_video(library, session)
    fake_suite.script += [propose("propose_cut", asset, 0, 1), reply("A cut.")]
    done = run(library, new_session(library))
    monkeypatch.setattr(capacity, "has_room", lambda needed_bytes=0: False)
    (result,) = library.post(f"/api/assistant/turns/{done['id']}/apply").json()["data"]
    assert result["applied"] == [] and result["skipped"] == [capacity.DISK_FULL_MESSAGE]


# ─── choosing what to apply ──────────────────────────────────────────────────


@needs_ffmpeg
def test_edits_and_clips_are_chosen_separately(library, session, fake_suite):
    asset = a_video(library, session)

    def a_turn():
        fake_suite.script += [
            reply("", ("propose_changes", {"asset_id": asset.id, "name": "Welcome film"}),
                  ("propose_clip", {"asset_id": asset.id, "start_seconds": 0, "end_seconds": 1})),
            reply("A rename and a clip."),
        ]
        return run(library, new_session(library))

    # Only the clip: the rename is left.
    done = a_turn()
    clip_id = done["proposal"]["clips"][0]["clip_id"]
    results = library.post(f"/api/assistant/turns/{done['id']}/apply",
                           json={"asset_ids": [], "clip_ids": [clip_id]}).json()["data"]
    assert [r["clip_id"] for r in results] == [clip_id]
    session.refresh(asset)
    assert asset.name != "Welcome film"

    # Only the rename: a client that names assets has never heard of clips, and gets none.
    done = a_turn()
    results = library.post(f"/api/assistant/turns/{done['id']}/apply",
                           json={"asset_ids": [asset.id]}).json()["data"]
    assert [r["clip_id"] for r in results] == [None]
    session.refresh(asset)
    assert asset.name == "Welcome film"
    assert len(made_clips(library, asset)) == 1  # still only the first turn's


@needs_ffmpeg
def test_a_clip_for_a_video_deleted_since_is_skipped_and_the_rest_stands(
        library, session, fake_suite):
    asset = a_video(library, session)
    other = a_video(library, session)
    fake_suite.script += [
        reply("", ("propose_clip", {"asset_id": asset.id, "start_seconds": 0, "end_seconds": 1}),
              ("propose_clip", {"asset_id": other.id, "start_seconds": 0, "end_seconds": 1})),
        reply("Two clips."),
    ]
    done = run(library, new_session(library))
    assert library.delete(f"/api/assets/{asset.id}").status_code in (200, 204)
    first, second = library.post(f"/api/assistant/turns/{done['id']}/apply").json()["data"]
    assert first["skipped"] == ["The asset no longer exists."] and first["applied"] == []
    assert second["applied"] == ["clip made"]


def test_a_turn_from_before_clips_existed_still_applies(library, session, fake_suite):
    """Stored proposals have no `clips` key; they must still read and apply."""
    from app.assistant import apply as apply_service

    asset = an_asset(library, session)
    fake_suite.script += [
        reply("", ("propose_changes", {"asset_id": asset.id, "name": "Old style"})), reply("Ok.")]
    done = run(library, new_session(library))
    proposal = apply_service.decode(json.dumps({"items": done["proposal"]["items"]}))
    results = apply_service.apply(session, "user-under-test", proposal)
    assert [r["applied"] for r in results] == [["name"]]
