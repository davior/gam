"""What an audio file is (music, speech, a sound effect) and what that decides.

The rule under test: only a confident "music" or "sound effect" skips transcription.
Unknown keeps the behaviour every audio file had before the kind existed, because a
voice recording losing its transcript is the costly mistake.
"""

import shutil
from pathlib import Path

import pytest
from sqlmodel import select

from app import music
from app.ingest import web
from app.models.asset import Asset
from app.models.job import KIND_ANALYZE_MUSIC, KIND_TRANSCRIBE, EnrichmentJob
from app.settings_store import DEEPGRAM_API_KEY, set_setting

FIXTURES = Path(__file__).parent / "fixtures"
TEST_USER = "user-under-test"


@pytest.fixture
def deepgram(session):
    """A Deepgram key, so transcription would be queued unless something stops it."""
    set_setting(session, TEST_USER, DEEPGRAM_API_KEY, "dg-test-key")


def _upload(client, name: str) -> dict:
    files = [("files", (name, (FIXTURES / name).read_bytes(), "application/octet-stream"))]
    return client.post("/api/assets", files=files).json()["created"][0]


def _jobs(session, asset_id: str, kind: str) -> list[EnrichmentJob]:
    return session.exec(
        select(EnrichmentJob).where(EnrichmentJob.asset_id == asset_id, EnrichmentJob.kind == kind)
    ).all()


# ─── from tags ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "genres, bpm, expected",
    [
        (["Ambient"], None, "music"),
        ([], 92.0, "music"),
        (["Podcast"], None, "speech"),
        (["Speech"], 120.0, "speech"),  # a speech genre wins over anything else
        (["Spoken Word", "Jazz"], None, "speech"),
        ([], None, None),
    ],
)
def test_kind_from_tags(genres, bpm, expected):
    assert music.kind_from_tags(genres, bpm) == expected


def test_a_tagged_song_is_music_and_is_not_transcribed(library, session, deepgram):
    """music_audio.mp3 carries an ID3 genre and TBPM."""
    asset = _upload(library, "music_audio.mp3")

    assert asset["audio_kind"] == "music"
    assert _jobs(session, asset["id"], KIND_TRANSCRIBE) == []


def test_an_untagged_recording_is_unknown_and_is_still_transcribed(library, session, deepgram):
    asset = _upload(library, "sample_audio.mp3")

    assert asset["audio_kind"] is None
    assert len(_jobs(session, asset["id"], KIND_TRANSCRIBE)) == 1


def test_a_video_has_no_audio_kind(library, session):
    asset = _upload(library, "sample_video.mp4")
    assert asset["audio_kind"] is None
    response = library.patch(f"/api/assets/{asset['id']}", json={"audio_kind": "music"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "not_audio"


# ─── by hand ─────────────────────────────────────────────────────────────────


def test_marking_music_as_speech_transcribes_it(library, session, deepgram):
    asset = _upload(library, "music_audio.mp3")
    assert _jobs(session, asset["id"], KIND_TRANSCRIBE) == []

    data = library.patch(f"/api/assets/{asset['id']}", json={"audio_kind": "speech"}).json()["data"]

    assert data["audio_kind"] == "speech"
    assert len(_jobs(session, asset["id"], KIND_TRANSCRIBE)) == 1


def test_marking_speech_as_music_measures_it(library, session, tmp_path):
    from tests import music_synth as synth

    # Long enough to be analysable, and tagged nothing, so it starts unknown.
    path = synth.write_mp3(synth.song(100, 0, "major", seconds=12), tmp_path / "song.mp3")
    files = [("files", ("song.mp3", path.read_bytes(), "audio/mpeg"))]
    asset = library.post("/api/assets", files=files).json()["created"][0]
    library.patch(f"/api/assets/{asset['id']}", json={"audio_kind": "speech"})
    for job in _jobs(session, asset["id"], KIND_ANALYZE_MUSIC):
        session.delete(job)
    session.commit()

    library.patch(f"/api/assets/{asset['id']}", json={"audio_kind": "music"})

    assert len(_jobs(session, asset["id"], KIND_ANALYZE_MUSIC)) == 1


def test_a_speech_file_is_not_analysed_as_music(library, session, tmp_path):
    import subprocess

    from tests import music_synth as synth

    path = synth.write_mp3(synth.speechlike(seconds=12), tmp_path / "talk.mp3")
    # Tagged as a podcast, as a podcast tool would.
    tagged = tmp_path / "talk_tagged.mp3"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path), "-c", "copy",
         "-metadata", "genre=Podcast", str(tagged)],
        check=True,
    )
    files = [("files", ("talk.mp3", tagged.read_bytes(), "audio/mpeg"))]
    asset = library.post("/api/assets", files=files).json()["created"][0]

    assert asset["audio_kind"] == "speech"
    assert _jobs(session, asset["id"], KIND_ANALYZE_MUSIC) == []


def test_an_invalid_kind_is_refused(library):
    asset = _upload(library, "sample_audio.mp3")
    response = library.patch(f"/api/assets/{asset['id']}", json={"audio_kind": "podcast"})
    assert response.status_code == 422


def test_clearing_the_kind_is_allowed(library):
    asset = _upload(library, "music_audio.mp3")
    data = library.patch(f"/api/assets/{asset['id']}", json={"audio_kind": None}).json()["data"]
    assert data["audio_kind"] is None


# ─── page import ─────────────────────────────────────────────────────────────


def test_a_track_from_a_music_page_is_music_and_is_not_transcribed(
    library, session, deepgram, monkeypatch
):
    def download_audio(url, workdir, stem, **_kwargs):
        target = Path(workdir) / f"{stem}.mp3"
        shutil.copy(FIXTURES / "sample_audio.mp3", target)  # untagged: tags say nothing
        return target

    monkeypatch.setattr(web, "download_audio", download_audio)
    from app.jobs import enrichment as enrichment_jobs

    track = {"audio_url": "https://a.example.org/song.mp3", "title": "Song"}
    job = library.post("/api/assets/import-page", json={"tracks": [track]}).json()["data"][0]
    monkeypatch.setattr(enrichment_jobs.queue(), "engine", session.get_bind())
    enrichment_jobs._run_job(job["id"])
    session.expire_all()

    asset = session.exec(select(Asset).where(Asset.name == "Song")).one()
    assert asset.audio_kind == "music"
    assert _jobs(session, asset.id, KIND_TRANSCRIBE) == []
