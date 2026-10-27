"""Importing tracks from a page the person saved, with the files they downloaded.

For sites that refuse GAM's own requests (Pixabay answers a server with 403). Nothing
here touches the network: the tests hand in a saved page and some audio files, the way
the person would.
"""

import json
import shutil
from pathlib import Path

from sqlmodel import select

from app import music
from app.ingest import page_tracks
from app.models.asset import PROVENANCE_EMBEDDED, Asset
from app.models.job import KIND_ANALYZE_MUSIC, KIND_TRANSCRIBE, EnrichmentJob
from app.settings_store import DEEPGRAM_API_KEY, set_setting

PAGES = Path(__file__).parent / "fixtures" / "pages"
FIXTURES = Path(__file__).parent / "fixtures"
TEST_USER = "user-under-test"


def _page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


def _audio(tmp_path: Path, name: str, source: str = "sample_audio.mp3") -> Path:
    target = tmp_path / name
    shutil.copy(FIXTURES / source, target)
    return target


def _post(client, page: str, files: list[Path]):
    upload = [("page", ("saved.html", (PAGES / page).read_bytes(), "text/html"))]
    upload += [("files", (f.name, f.read_bytes(), "audio/mpeg")) for f in files]
    return client.post("/api/assets/import-page/saved", files=upload)


# ─── reading the saved page ──────────────────────────────────────────────────


def test_a_saved_pixabay_page_gives_each_track_and_its_id():
    found = page_tracks.extract_saved(_page("pixabay_saved.html"))

    assert found.source == "pixabay"
    assert found.page_url == "https://pixabay.com/music/search/mood/mysterious/?pagi=2"
    first = found.tracks[0]
    assert first.title == "Investigation"
    assert first.audio_url is None
    assert first.match_id == "604094"
    assert first.creator == "leberch" and first.publisher == "Pixabay"
    assert first.source_url == "https://pixabay.com/music/crime-scene-investigation-604094/"
    assert first.duration_seconds == 264
    assert first.genres == ["Crime Scene"]
    # The listing is the "mysterious" mood, so every track on it is.
    assert first.moods == ["Mysterious"]
    assert first.tags == ["Investigation"]
    assert "Pixabay Content License" in first.license


def test_a_saved_mixkit_page_works_without_the_network():
    """A saved copy has no canonical link here, so the JSON-LD still has to carry it."""
    found = page_tracks.extract_saved(_page("mixkit_mood.html"))
    assert [t.title for t in found.tracks] == ["I'm Still Waitin'", "Bigger than Texas"]


def test_a_saved_incompetech_page_says_what_it_needs():
    page = _page("incompetech_music.html").replace(
        "<html>",
        '<html><head><link rel="canonical" '
        'href="https://incompetech.com/music/royalty-free/music.html"></head>',
    )
    found = page_tracks.extract_saved(page)
    assert found.tracks == []
    assert "pieces.json" in found.note


def test_the_page_address_comes_from_the_comment_chrome_writes():
    page = "<!-- saved from url=(0040)https://example.org/music/list.html -->\n<html></html>"
    assert page_tracks.saved_page_url(page) == "https://example.org/music/list.html"


# ─── matching files to tracks ────────────────────────────────────────────────


def test_files_match_by_id_then_by_title():
    tracks = page_tracks.extract_saved(_page("pixabay_saved.html")).tracks
    matched = page_tracks.match_files(
        tracks,
        [
            "leberch-investigation-604094.mp3",  # by Pixabay's id
            "Dark Ambient.mp3",  # by title
            "my tension riser copy.mp3",  # the longer title, not "Riser"
            "riser.mp3",
            "something else.mp3",
        ],
    )
    titles = {name: (t.title if t else None) for name, t in matched.items()}
    assert titles == {
        "leberch-investigation-604094.mp3": "Investigation",
        "Dark Ambient.mp3": "Dark Ambient",
        "my tension riser copy.mp3": "Tension Riser",
        "riser.mp3": "Riser",
        "something else.mp3": None,
    }


def test_one_track_is_matched_to_one_file():
    tracks = page_tracks.extract_saved(_page("pixabay_saved.html")).tracks
    matched = page_tracks.match_files(tracks, ["Dark Ambient.mp3", "Dark Ambient (1).mp3"])
    assert [t and t.title for t in matched.values()] == ["Dark Ambient", None]


# ─── the API ─────────────────────────────────────────────────────────────────


def test_the_preview_lists_the_tracks(library):
    response = library.post(
        "/api/assets/import-page/saved/preview",
        files=[("page", ("saved.html", (PAGES / "pixabay_saved.html").read_bytes(), "text/html"))],
    )
    body = response.json()["data"]
    assert body["source"] == "pixabay" and len(body["tracks"]) == 4
    assert body["tracks"][0]["match_id"] == "604094"
    assert body["tracks"][0]["audio_url"] is None


def test_downloaded_files_are_imported_with_their_page_details(library, session, tmp_path):
    set_setting(session, TEST_USER, DEEPGRAM_API_KEY, "dg-test-key")
    from tests import music_synth as synth

    # A real-length track, so the analysis is queued for what the page left out.
    track = synth.write_mp3(
        synth.song(100, 0, "major", seconds=12), tmp_path / "leberch-investigation-604094.mp3"
    )
    response = _post(library, "pixabay_saved.html", [track])

    [result] = response.json()["data"]["results"]
    assert result["status"] == "imported" and result["track_title"] == "Investigation"
    asset = session.get(Asset, result["asset_id"])
    assert asset.name == "Investigation"
    assert asset.creator == "leberch" and asset.publisher == "Pixabay"
    assert asset.source_url == "https://pixabay.com/music/crime-scene-investigation-604094/"
    assert music.decode_terms(asset.music_moods) == ["Mysterious"]
    assert json.loads(asset.field_provenance)["music_genres"] == PROVENANCE_EMBEDDED
    assert asset.audio_kind == "music"

    jobs = {
        j.kind
        for j in session.exec(select(EnrichmentJob).where(EnrichmentJob.asset_id == asset.id)).all()
    }
    assert KIND_TRANSCRIBE not in jobs and KIND_ANALYZE_MUSIC in jobs


def test_a_file_not_on_the_page_is_imported_without_details(library, session, tmp_path):
    response = _post(library, "pixabay_saved.html", [_audio(tmp_path, "my own.mp3")])
    [result] = response.json()["data"]["results"]
    assert result["status"] == "unmatched"
    asset = session.get(Asset, result["asset_id"])
    assert asset.audio_kind == "music" and asset.creator is None


def test_a_file_already_in_the_library_is_not_stored_twice(library, session, tmp_path):
    files = [("files", ("mine.mp3", (FIXTURES / "sample_audio.mp3").read_bytes(), "audio/mpeg"))]
    mine = library.post("/api/assets", files=files).json()["created"][0]

    response = _post(library, "pixabay_saved.html", [_audio(tmp_path, "Dark Ambient.mp3")])

    [result] = response.json()["data"]["results"]
    assert result["status"] == "duplicate" and result["asset_id"] == mine["id"]
    assert len(session.exec(select(Asset)).all()) == 1
    # The page still fills the copy's blanks.
    assert session.get(Asset, mine["id"]).creator == "AtlasAudio"


def test_a_file_that_is_not_audio_is_refused(library, session, tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("not a track")
    [result] = _post(library, "pixabay_saved.html", [notes]).json()["data"]["results"]
    assert result["status"] == "rejected"
    assert session.exec(select(Asset)).all() == []


def test_too_many_files_at_once_are_refused(library, tmp_path):
    from app.enrichment import page_import

    track = _audio(tmp_path, "t.mp3")
    response = _post(library, "pixabay_saved.html", [track] * (page_import.MAX_TRACKS_PER_IMPORT + 1))
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "too_many_files"
