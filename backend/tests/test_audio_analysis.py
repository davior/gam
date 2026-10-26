"""Measuring tempo, key and energy from sound, and filling the music profile with it.

The audio is synthesised (`tests/music_synth.py`) with a tempo and key stated in the
test, so what each assertion expects is in plain sight.
"""

import json

import numpy as np
import pytest
from sqlmodel import select

from app.ingest.audio_analysis import AudioAnalysisError, analyse, decode
from app.models.asset import PROVENANCE_ANALYSIS, PROVENANCE_EMBEDDED, Asset
from app.models.job import KIND_ANALYZE_MUSIC, KIND_ANALYZE_MUSIC_LIBRARY, EnrichmentJob
from tests import music_synth as synth

# ─── the measurement ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("bpm", [92, 100, 120, 128, 140])
def test_the_tempo_of_a_beat_is_measured(bpm):
    result = analyse(synth.song(bpm, 0, "major", seed=bpm))
    assert result.bpm == pytest.approx(bpm, abs=1.5)


@pytest.mark.parametrize("bpm", [72, 170])
def test_a_slow_or_fast_beat_may_read_an_octave_off_but_never_elsewhere(bpm):
    """The ambiguity every tempo method has. A 72 BPM track with eighth-note hats also
    pulses at 144; a 170 BPM one shares every other beat with 85. An octave is
    acceptable; any other answer is not."""
    result = analyse(synth.song(bpm, 0, "major", seed=bpm))
    assert any(result.bpm == pytest.approx(target, abs=1.5) for target in (bpm, 2 * bpm, bpm / 2))


@pytest.mark.parametrize("tonic, mode", [(0, "major"), (7, "major"), (9, "minor"), (2, "minor")])
def test_the_key_is_measured(tonic, mode):
    result = analyse(synth.song(100, tonic, mode, seed=tonic))
    assert result.key == f"{synth.PITCH_NAMES[tonic]} {mode}"


def test_a_beatless_pad_has_a_key_and_no_tempo():
    """No tempo is better than a confident wrong one under ambient music."""
    result = analyse(synth.pad(2, "minor", seed=3))
    assert result.bpm is None
    assert result.key == "D minor"
    assert result.is_music
    assert result.energy == "low"


def test_a_drum_track_has_more_energy_than_a_pad():
    beat = analyse(synth.song(140, 0, "major"))
    calm = analyse(synth.pad(0, "major"))
    assert beat.energy_score > calm.energy_score
    assert beat.energy in ("medium", "high") and calm.energy == "low"


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_speech_is_not_music(seed):
    """A talk must not be filed under a key and an energy."""
    assert not analyse(synth.speechlike(seed=seed)).is_music


def test_noise_and_silence_are_not_music():
    noise = (np.random.default_rng(0).standard_normal(synth.SAMPLE_RATE * 20) * 0.1).astype(
        np.float32
    )
    assert not analyse(noise).is_music
    silence = np.zeros(synth.SAMPLE_RATE * 20, dtype=np.float32)
    assert analyse(silence) == analyse(silence)  # no division by zero, no NaN
    assert not analyse(silence).is_music


def test_too_short_to_measure_is_an_error():
    with pytest.raises(AudioAnalysisError):
        analyse(np.zeros(synth.SAMPLE_RATE * 3, dtype=np.float32))


def test_an_encoded_file_decodes_to_the_same_answer(tmp_path):
    """Through ffmpeg and MP3 and back, the way an uploaded file is read."""
    path = synth.write_mp3(synth.song(120, 9, "minor"), tmp_path / "song.mp3")
    samples = decode(path, duration_seconds=30.0)
    assert samples.size == pytest.approx(30 * synth.SAMPLE_RATE, rel=0.02)
    result = analyse(samples)
    assert result.bpm == pytest.approx(120, abs=1.5)
    assert result.key == "A minor"


# ─── the job and the API ─────────────────────────────────────────────────────


def _upload(client, path):
    files = [("files", (path.name, path.read_bytes(), "audio/mpeg"))]
    return client.post("/api/assets", files=files).json()["created"][0]


def _run_job(job_id, session, monkeypatch):
    """Run a queued job against the test database (see test_attribution_api.py)."""
    from app.jobs import enrichment as enrichment_jobs

    monkeypatch.setattr(enrichment_jobs.queue(), "engine", session.get_bind())
    enrichment_jobs._run_job(job_id)
    session.expire_all()


def _job_for(session, asset_id, kind=KIND_ANALYZE_MUSIC):
    return session.exec(
        select(EnrichmentJob).where(EnrichmentJob.asset_id == asset_id, EnrichmentJob.kind == kind)
    ).first()


def test_an_audio_upload_is_analysed_without_being_asked(library, session, monkeypatch, tmp_path):
    asset = _upload(library, synth.write_mp3(synth.song(92, 7, "major"), tmp_path / "theme.mp3"))

    job = _job_for(session, asset["id"])
    assert job is not None and job.status == "queued"
    _run_job(job.id, session, monkeypatch)

    stored = session.get(Asset, asset["id"])
    assert stored.music_bpm == pytest.approx(92, abs=1.5)
    assert stored.music_key == "G major"
    assert stored.music_energy in ("low", "medium", "high")
    provenance = json.loads(stored.field_provenance)
    assert provenance["music_bpm"] == PROVENANCE_ANALYSIS
    assert session.get(EnrichmentJob, job.id).detail.startswith("92")


def test_a_tag_is_kept_over_a_measurement(library, session, monkeypatch):
    """The ID3 fixture says 92 BPM and Am, and whoever tagged it heard the track."""
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "music_audio.mp3"
    files = [("files", (fixture.name, fixture.read_bytes(), "audio/mpeg"))]
    created = library.post("/api/assets", files=files).json()["created"][0]
    stored = session.get(Asset, created["id"])
    # One second of sine: too short to measure, so the job is not even queued.
    assert _job_for(session, stored.id) is None
    assert stored.music_key == "Am"
    assert json.loads(stored.field_provenance)["music_key"] == PROVENANCE_EMBEDDED


def test_a_measurement_never_overwrites_a_hand_edit(library, session, monkeypatch, tmp_path):
    asset = _upload(library, synth.write_mp3(synth.song(128, 0, "major"), tmp_path / "a.mp3"))
    library.patch(f"/api/assets/{asset['id']}", json={"music_key": "C major", "music_bpm": 64})

    _run_job(_job_for(session, asset["id"]).id, session, monkeypatch)

    stored = session.get(Asset, asset["id"])
    assert stored.music_bpm == 64 and stored.music_key == "C major"
    # The blank one still fills.
    assert stored.music_energy is not None


def test_speech_fills_nothing(library, session, monkeypatch, tmp_path):
    asset = _upload(library, synth.write_mp3(synth.speechlike(), tmp_path / "talk.mp3"))
    job = _job_for(session, asset["id"])
    _run_job(job.id, session, monkeypatch)

    stored = session.get(Asset, asset["id"])
    assert (stored.music_bpm, stored.music_key, stored.music_energy) == (None, None, None)
    assert "No beat or key" in session.get(EnrichmentJob, job.id).detail


def test_the_button_analyses_one_track(library, session, tmp_path):
    asset = _upload(library, synth.write_mp3(synth.song(100, 0, "major"), tmp_path / "a.mp3"))
    # The upload queued one; a second while it is still queued is refused.
    assert library.post(f"/api/assets/{asset['id']}/analyze-music").status_code == 409

    session.delete(_job_for(session, asset["id"]))
    session.commit()
    response = library.post(f"/api/assets/{asset['id']}/analyze-music")
    assert response.status_code == 202
    assert response.json()["data"]["action"] == KIND_ANALYZE_MUSIC


def test_only_audio_is_analysed(library):
    from pathlib import Path

    video = Path(__file__).parent / "fixtures" / "sample_video.mp4"
    files = [("files", (video.name, video.read_bytes(), "video/mp4"))]
    asset = library.post("/api/assets", files=files).json()["created"][0]
    response = library.post(f"/api/assets/{asset['id']}/analyze-music")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "not_analysable"


def test_the_library_run_fills_what_uploads_before_it_missed(
    library, session, monkeypatch, tmp_path
):
    song = _upload(library, synth.write_mp3(synth.song(120, 9, "minor"), tmp_path / "s.mp3"))
    talk = _upload(library, synth.write_mp3(synth.speechlike(), tmp_path / "t.mp3"))
    # As if uploaded before analysis existed: no job ran.
    for job in session.exec(select(EnrichmentJob)).all():
        session.delete(job)
    session.commit()

    response = library.post("/api/assets/analyze-music")
    assert response.status_code == 202
    assert library.post("/api/assets/analyze-music").status_code == 409

    job = session.exec(
        select(EnrichmentJob).where(EnrichmentJob.kind == KIND_ANALYZE_MUSIC_LIBRARY)
    ).one()
    _run_job(job.id, session, monkeypatch)

    assert session.get(Asset, song["id"]).music_key == "A minor"
    assert session.get(Asset, talk["id"]).music_key is None
    detail = session.get(EnrichmentJob, job.id).detail
    assert detail == "1 filled in of 2 analysed, 1 with no beat or key"
