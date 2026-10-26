"""The music profile: parsing, the harvest from real tags, the API, and search.

The harvest tests run against real files built by `tests/make_fixtures.py`, for the
reason test_embedded_metadata.py gives: what a format stores and where is the thing most
likely to be wrong, and a mocked tag dict cannot be wrong about it.
"""

import json
from pathlib import Path

import pytest

from app import music
from app.ingest import ytdlp
from app.ingest.embedded_metadata import harvest
from app.ingest.probe import probe
from app.models.asset import PROVENANCE_EMBEDDED, PROVENANCE_HUMAN, Asset

FIXTURES = Path(__file__).parent / "fixtures"
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _harvest(name: str, asset_type: str = "audio") -> dict:
    path = FIXTURES / name
    return harvest(path, asset_type, name, probe_tags=probe(path).tags)


def _upload(client, name: str) -> dict:
    files = [("files", (name, (FIXTURES / name).read_bytes(), "application/octet-stream"))]
    return client.post("/api/assets", files=files).json()["created"][0]


def _search_ids(client, q: str) -> list[str]:
    body = client.get("/api/search", params={"q": q}).json()
    return [hit["asset"]["id"] for hit in body["data"]]


# ─── parsing ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Ambient;Cinematic", ["Ambient", "Cinematic"]),
        ("Hip-Hop/Rap", ["Hip-Hop", "Rap"]),
        ("(17)Rock", ["Rock"]),
        ("(17)", []),
        (" ambient ; Ambient ", ["ambient"]),
        ("", []),
        (None, []),
    ],
)
def test_split_terms(raw, expected):
    assert music.split_terms(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("92", 92.0),
        ("92.54", 92.5),
        ("120 BPM", 120.0),
        ("128,0", 128.0),
        (140, 140.0),
        # A track number or a year in the wrong frame is not a tempo.
        ("3", None),
        ("2019", None),
        ("fast", None),
        (True, None),
        (None, None),
    ],
)
def test_parse_bpm(raw, expected):
    assert music.parse_bpm(raw) == expected


def test_a_key_is_kept_in_its_own_notation():
    assert music.clean_key("  A  minor ") == "A minor"
    assert music.clean_key("8A") == "8A"
    assert music.clean_key("a comment that ended up in the key frame") is None
    assert music.clean_key("") is None


def test_an_empty_list_is_stored_as_null_not_as_an_answer():
    assert music.encode_terms([]) is None
    assert music.encode_terms(["  "]) is None
    assert json.loads(music.encode_terms(["Ambient", "ambient", "Drone"])) == ["Ambient", "Drone"]


def test_a_malformed_list_column_reads_as_empty():
    assert music.decode_terms("not json") == []
    assert music.decode_terms('{"a": 1}') == []
    assert music.decode_terms(None) == []


def test_index_text_uses_the_words_people_search_with():
    asset = Asset(
        user_id="u",
        name="x",
        asset_type="audio",
        music_genres='["Ambient"]',
        music_moods='["Tense"]',
        music_instruments='["Piano"]',
        music_bpm=92.0,
        music_key="Am",
        music_vocals=False,
        music_energy="low",
    )
    text = music.index_text(asset)
    assert text == "Ambient Tense Piano instrumental low energy"
    # A bare number collides with titles; the filters, not the text index, serve it.
    assert "92" not in text


def test_unknown_vocals_say_nothing():
    """NULL must not index as "instrumental", or every untagged song would match it."""
    asset = Asset(user_id="u", name="x", asset_type="audio")
    assert music.index_text(asset) == ""


# ─── the harvest, from real tags ─────────────────────────────────────────────


def test_id3_music_frames_are_read():
    found = _harvest("music_audio.mp3")
    assert json.loads(found["music_genres"]) == ["Ambient", "Cinematic"]
    assert json.loads(found["music_moods"]) == ["Tense"]
    assert found["music_bpm"] == 92.0
    assert found["music_key"] == "Am"
    # No lyrics frame, and no genre saying "Instrumental": not known either way.
    assert "music_vocals" not in found
    # The attribution beside it still comes through.
    assert found["creator"] == "Jane Doe"


def test_vorbis_comments_are_read():
    found = _harvest("music_audio.flac")
    assert json.loads(found["music_genres"]) == ["Folk"]
    assert json.loads(found["music_moods"]) == ["Uplifting"]
    assert found["music_bpm"] == 128.0
    assert found["music_key"] == "8A"
    assert found["music_vocals"] is True


def test_a_genre_of_instrumental_means_no_vocals():
    from app.ingest.embedded_metadata import _music_from_tags

    assert _music_from_tags({"genre": "Instrumental"})["music_vocals"] is False
    # A lyrics frame is the stronger evidence when a file carries both.
    assert _music_from_tags({"genre": "Instrumental", "lyrics-eng": "la"})["music_vocals"] is True


def test_an_untagged_file_has_no_profile():
    found = _harvest("sample_audio.mp3")
    assert not any(name in found for name in music.MUSIC_FIELDS)


def test_a_video_genre_is_not_read_as_music():
    """A video's genre is the programme's, not a description of its soundtrack."""
    from app.ingest.embedded_metadata import harvest as harvest_fn

    found = harvest_fn(Path("x.mp4"), "video", "x.mp4", probe_tags={"genre": "Documentary"})
    assert "music_genres" not in found


# ─── upload, API and search ──────────────────────────────────────────────────


def test_an_upload_carries_its_profile_through_the_api(library, session):
    asset = _upload(library, "music_audio.mp3")

    assert asset["music_genres"] == ["Ambient", "Cinematic"]
    assert asset["music_moods"] == ["Tense"]
    assert asset["music_bpm"] == 92.0
    assert asset["music_key"] == "Am"
    assert asset["music_vocals"] is None

    stored = session.get(Asset, asset["id"])
    provenance = json.loads(stored.field_provenance)
    assert provenance["music_genres"] == PROVENANCE_EMBEDDED
    assert provenance["music_bpm"] == PROVENANCE_EMBEDDED


def test_an_upload_is_findable_by_its_genre_and_mood(library):
    asset = _upload(library, "music_audio.mp3")
    assert asset["id"] in _search_ids(library, "cinematic")
    assert asset["id"] in _search_ids(library, "tense")


def test_a_profile_can_be_written_by_hand(library, session):
    asset = _upload(library, "sample_audio.mp3")

    response = library.patch(
        f"/api/assets/{asset['id']}",
        json={
            "music_genres": ["Orchestral", " orchestral ", "Score"],
            "music_instruments": ["Strings", "Brass"],
            "music_vocals": False,
            "music_energy": "high",
            "music_bpm": 140,
            "music_key": "D minor",
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["music_genres"] == ["Orchestral", "Score"]
    assert data["music_instruments"] == ["Strings", "Brass"]
    assert data["music_vocals"] is False
    assert data["music_energy"] == "high"
    assert data["music_bpm"] == 140.0
    assert data["music_key"] == "D minor"

    stored = session.get(Asset, asset["id"])
    assert json.loads(stored.field_provenance)["music_energy"] == PROVENANCE_HUMAN

    # Reindexed on the edit, in the words a search would use.
    assert asset["id"] in _search_ids(library, "instrumental")
    assert asset["id"] in _search_ids(library, "brass")


def test_an_empty_list_clears_a_profile_field(library, session):
    asset = _upload(library, "music_audio.mp3")

    data = library.patch(f"/api/assets/{asset['id']}", json={"music_genres": []}).json()["data"]
    assert data["music_genres"] == []
    assert session.get(Asset, asset["id"]).music_genres is None


@pytest.mark.parametrize(
    "body",
    [
        {"music_energy": "eleven"},
        {"music_bpm": 5},
        {"music_bpm": 900},
        {"music_genres": ["x" * 61]},
        {"music_moods": [f"m{i}" for i in range(21)]},
        {"music_key": "x" * 17},
    ],
)
def test_a_bad_profile_value_is_refused(library, body):
    asset = _upload(library, "sample_audio.mp3")
    assert library.patch(f"/api/assets/{asset['id']}", json=body).status_code == 422


def test_a_harvest_never_overwrites_a_hand_written_profile(library, session):
    from app.services import assets as asset_service

    asset = _upload(library, "music_audio.mp3")
    library.patch(f"/api/assets/{asset['id']}", json={"music_moods": ["Melancholy"]})
    stored = session.get(Asset, asset["id"])

    asset_service.apply_embedded_fields(
        session, stored, {"music_moods": '["Tense"]', "music_energy": "low"}
    )
    session.refresh(stored)

    assert music.decode_terms(stored.music_moods) == ["Melancholy"]
    # The blank one still fills.
    assert stored.music_energy == "low"


# ─── URL import ──────────────────────────────────────────────────────────────


def test_a_site_genre_is_carried_from_an_import():
    meta = ytdlp.metadata_from_info({"title": "Song", "genre": "Lo-fi; Chillhop"})
    assert json.loads(meta.music["music_genres"]) == ["Lo-fi", "Chillhop"]

    meta = ytdlp.metadata_from_info({"title": "Song", "genres": ["Folk", "Acoustic"]})
    assert json.loads(meta.music["music_genres"]) == ["Folk", "Acoustic"]

    assert ytdlp.metadata_from_info({"title": "Talk"}).music == {}


# ─── the migration ───────────────────────────────────────────────────────────


def test_the_migration_indexes_the_same_music_fields_this_module_does():
    """`d71e4b9a2c86` carries a literal copy of the music_text expression; this is what
    stops it drifting from `music.index_text`."""
    migration = (
        BACKEND_ROOT / "alembic" / "versions" / "20261026_0901_rebuild_asset_fts_with_music.py"
    ).read_text()
    for name in (*music.MUSIC_LIST_FIELDS, "music_vocals", "music_energy"):
        assert f"a.{name}" in migration, f"the migration does not index {name}"
    for name in ("music_bpm", "music_key"):
        assert f"a.{name}" not in migration, f"the migration indexes {name}, music.py does not"

