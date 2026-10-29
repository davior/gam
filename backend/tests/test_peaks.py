"""The audio player's waveform, over HTTP and against real media."""

import os
import time
from pathlib import Path

import numpy as np
import pytest

from app.ingest import waveform
from app.media_tools import ffmpeg_available
from app.models.asset import Asset
from app.storage import peaks_key_for

FIXTURES = Path(__file__).parent / "fixtures"

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def _upload(client, name: str, mime: str):
    return client.post(
        "/api/assets",
        files=[("files", (name, (FIXTURES / name).read_bytes(), mime))],
    ).json()["created"][0]


def _upload_audio(client):
    return _upload(client, "sample_audio.mp3", "audio/mpeg")


def _peaks_path(session, media_dir, asset_id: str) -> Path:
    return media_dir / peaks_key_for(session.get(Asset, asset_id).storage_key)


@needs_ffmpeg
def test_an_audio_asset_has_normalised_peaks(library):
    asset = _upload_audio(library)

    response = library.get(f"/api/assets/{asset['id']}/peaks")

    assert response.status_code == 200
    data = response.json()["data"]
    assert 0 < len(data["peaks"]) <= waveform.DEFAULT_BINS
    assert all(0.0 <= p <= 1.0 for p in data["peaks"])
    # Normalised: the loudest moment is drawn at full height.
    assert max(data["peaks"]) == 1.0
    assert data["duration"] == pytest.approx(asset["duration_seconds"], abs=0.2)


@needs_ffmpeg
def test_the_second_request_is_served_from_the_cache(library, session, media_dir, monkeypatch):
    asset = _upload_audio(library)
    first = library.get(f"/api/assets/{asset['id']}/peaks").json()["data"]
    assert _peaks_path(session, media_dir, asset["id"]).is_file()

    def no_decoding(*args, **kwargs):
        raise AssertionError("decoded again despite a cached waveform")

    monkeypatch.setattr(waveform, "compute", no_decoding)
    second = library.get(f"/api/assets/{asset['id']}/peaks").json()["data"]

    assert second == first


@needs_ffmpeg
def test_an_unreadable_cache_is_recomputed(library, session, media_dir):
    asset = _upload_audio(library)
    _peaks_path(session, media_dir, asset["id"]).write_text("{not json")

    response = library.get(f"/api/assets/{asset['id']}/peaks")

    assert response.status_code == 200
    assert response.json()["data"]["peaks"]


@needs_ffmpeg
def test_a_clip_shares_its_parents_waveform(library):
    parent = _upload_audio(library)
    clip = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    ).json()["data"]

    of_clip = library.get(f"/api/assets/{clip['id']}/peaks").json()["data"]
    of_parent = library.get(f"/api/assets/{parent['id']}/peaks").json()["data"]

    assert of_clip == of_parent


def test_an_image_has_no_waveform(library):
    image = _upload(library, "sample_image.jpg", "image/jpeg")

    response = library.get(f"/api/assets/{image['id']}/peaks")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "not_audio"


def test_someone_elses_audio_is_a_404(library, session):
    asset = _upload_audio(library)
    row = session.get(Asset, asset["id"])
    row.user_id = "someone-else"
    session.add(row)
    session.commit()

    response = library.get(f"/api/assets/{asset['id']}/peaks")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"


def test_a_missing_file_is_unavailable_not_an_error(library, session, media_dir):
    asset = _upload_audio(library)
    (media_dir / session.get(Asset, asset["id"]).storage_key).unlink()

    response = library.get(f"/api/assets/{asset['id']}/peaks")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "waveform_unavailable"


def test_without_ffmpeg_the_waveform_is_unavailable(library, monkeypatch):
    asset = _upload_audio(library)
    monkeypatch.setattr(waveform, "ffmpeg_available", lambda: False)

    response = library.get(f"/api/assets/{asset['id']}/peaks")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "waveform_unavailable"


@needs_ffmpeg
def test_deleting_the_asset_deletes_its_cached_waveform(library, session, media_dir):
    asset = _upload_audio(library)
    library.get(f"/api/assets/{asset['id']}/peaks")
    cached = _peaks_path(session, media_dir, asset["id"])
    assert cached.is_file()

    assert library.delete(f"/api/assets/{asset['id']}").status_code == 204

    assert not cached.exists()


@needs_ffmpeg
def test_deleting_a_clip_keeps_its_parents_waveform(library, session, media_dir):
    parent = _upload_audio(library)
    clip = library.post(
        f"/api/assets/{parent['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    ).json()["data"]
    library.get(f"/api/assets/{clip['id']}/peaks")
    cached = _peaks_path(session, media_dir, parent["id"])

    assert library.delete(f"/api/assets/{clip['id']}").status_code == 204

    assert cached.is_file()


@needs_ffmpeg
def test_a_cached_waveform_is_not_a_stray(library, session, media_dir):
    from app.services import storage_usage
    from app.storage import LocalStorage

    asset = _upload_audio(library)
    library.get(f"/api/assets/{asset['id']}/peaks")
    cached = _peaks_path(session, media_dir, asset["id"])
    old = time.time() - 3 * 24 * 3600
    os.utime(cached, (old, old))

    strays = list(storage_usage._scan(session, LocalStorage(media_dir)))

    assert cached.name not in {Path(key).name for key, _ in strays}


# ─── the reduction itself ────────────────────────────────────────────────────


def test_reduce_keeps_a_short_transient():
    """Max-pooled, not averaged: one loud block among quiet ones still reaches full
    height, which is what makes a clap findable by eye."""
    envelope = np.full(10_000, 0.01, dtype=np.float32)
    envelope[5_000] = 0.5

    peaks = waveform._reduce(envelope, 100)

    assert len(peaks) == 100
    assert max(peaks) == 1.0
    assert sorted(peaks)[-2] == pytest.approx(0.02, abs=0.001)


def test_reduce_leaves_a_short_envelope_at_its_own_length():
    peaks = waveform._reduce(np.array([0.0, 0.25, 0.5], dtype=np.float32), 2000)

    assert peaks == [0.0, 0.5, 1.0]


def test_reduce_of_silence_is_flat_not_a_division_by_zero():
    assert waveform._reduce(np.zeros(50, dtype=np.float32), 10) == [0.0] * 10
