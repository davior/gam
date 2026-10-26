"""Importing tracks from a music library's page: finding them, choosing, downloading.

The pages are trimmed copies of real ones (`tests/fixtures/pages/`), kept to the
structure each adapter reads. The network is replaced at `ingest/web.py`: the page
fetch returns a fixture, and a track download writes a real MP3, so the import runs
through the same ingest an upload does.
"""

import json
import shutil
from pathlib import Path

import httpx
import pytest
from sqlmodel import select

from app import music
from app.enrichment import page_import
from app.ingest import page_tracks, web
from app.models.asset import PROVENANCE_EMBEDDED, Asset
from app.models.job import KIND_IMPORT_TRACK, EnrichmentJob
from app.providers.base import Completion, Usage

PAGES = Path(__file__).parent / "fixtures" / "pages"
SAMPLE_MP3 = Path(__file__).parent / "fixtures" / "sample_audio.mp3"

MIXKIT_URL = "https://mixkit.co/free-stock-music/mood/carefree/?page=2"
INCOMPETECH_URL = "https://incompetech.com/music/royalty-free/music.html"
PLAIN_URL = "https://music.example.org/free/"


def _read(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


@pytest.fixture
def pages(monkeypatch):
    """Serve the fixture pages in place of the network. Returns the URLs asked for."""
    served = {
        MIXKIT_URL: _read("mixkit_mood.html"),
        INCOMPETECH_URL: _read("incompetech_music.html"),
        "https://incompetech.com/music/royalty-free/pieces.json": _read("incompetech_pieces.json"),
        PLAIN_URL: _read("plain_links.html"),
    }
    asked: list[str] = []

    def fetch_text(url, **_kwargs):
        asked.append(url)
        if url not in served:
            raise web.WebFetchError("The site has no such page (HTTP 404)")
        return url, served[url]

    monkeypatch.setattr(web, "fetch_text", fetch_text)
    return asked


def _no_structure(url, **_kwargs):
    raise AssertionError(f"unexpected fetch of {url}")


# ─── finding the tracks ──────────────────────────────────────────────────────


def test_mixkit_tracks_come_from_its_structured_data_and_cards():
    found = page_tracks.extract_structured(MIXKIT_URL, _read("mixkit_mood.html"), _no_structure)

    assert found.source == "mixkit" and found.method == page_tracks.METHOD_STRUCTURED
    first, second = found.tracks
    assert first.title == "I'm Still Waitin'"
    assert first.audio_url == "https://assets.mixkit.co/music/910/910.mp3"
    assert first.creator == "Michael Ramir C."
    assert first.publisher == "Mixkit"
    assert first.duration_seconds == 158
    assert first.published_date == "2020-01-28"
    assert first.genres == ["Folk Pop"]
    # The card's mood, plus the mood the listing itself is filtered to.
    assert first.moods == ["Warm", "Carefree"]
    assert first.instruments == ["Acoustic Guitar"]
    assert first.tags == ["Road Trip"]
    assert "Mixkit Stock Music Free License" in first.license
    # No page of its own, so the file is the source, and what recognises a re-import.
    assert first.source_url == first.audio_url
    # Matched by MP3, not by position: the second card's links stay with the second track.
    assert second.genres == ["Country", "Folk"]
    assert second.instruments == ["Percussion"]


def test_incompetech_tracks_come_from_the_file_its_page_loads():
    asked = []

    def fetch(url):
        asked.append(url)
        return url, _read("incompetech_pieces.json")

    found = page_tracks.extract_structured(INCOMPETECH_URL, _read("incompetech_music.html"), fetch)

    assert asked == ["https://incompetech.com/music/royalty-free/pieces.json"]
    assert found.source == "incompetech"
    cipher, gymnopedie = found.tracks  # the entry with no file is skipped
    assert cipher.audio_url == (
        "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Cipher.mp3"
    )
    assert cipher.creator == "Kevin MacLeod"
    assert cipher.genres == ["Electronica"]
    assert cipher.source_title == "Hard Electronic"
    assert cipher.moods == ["Dark", "Driving", "Mysterious"]
    assert cipher.instruments == ["Synths", "Drums"]
    assert cipher.bpm == 128
    assert cipher.duration_seconds == 185
    assert cipher.description == "Dark pulsing electronica."
    # The licence's own attribution wording, verbatim.
    assert cipher.credit_line.startswith('"Cipher" Kevin MacLeod (incompetech.com) Licensed')
    assert cipher.license == "CC BY 4.0"
    # A space in the filename is encoded; "Unclassifiable" is no genre; no album.
    assert gymnopedie.audio_url.endswith("/Gymnopedie%20No%201.mp3")
    assert gymnopedie.genres == [] and gymnopedie.source_title is None
    assert gymnopedie.bpm is None and gymnopedie.published_date == "2010"


def test_a_page_with_no_structure_falls_back_to_its_audio_links():
    found = page_tracks.extract_links(PLAIN_URL, _read("plain_links.html"))
    assert [(t.title, t.audio_url) for t in found.tracks] == [
        ("Morning Light", "https://music.example.org/files/Morning%20Light.mp3"),
        ("Night Drive", "https://cdn.example.org/tracks/night_drive.ogg"),
        ("ambient bed", "https://music.example.org/files/ambient-bed.wav"),
    ]


def test_page_text_keeps_link_targets_for_a_model():
    text = page_tracks.page_text(_read("plain_links.html"))
    assert "Morning Light [link: /files/Morning%20Light.mp3]" in text
    assert "[audio: /files/ambient-bed.wav]" in text
    assert "<script" not in text


# ─── the AI fallback ─────────────────────────────────────────────────────────


class FakeProvider:
    model = "fake-model"
    provider_type = "anthropic"
    supports_images = False

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def complete(self, prompt, *, system=None, images=()):
        self.prompts.append(prompt)
        return Completion(self.reply, Usage(1000, 200), self.model, self.provider_type)


def test_the_ai_reads_a_page_with_no_structured_data(library, session, pages, monkeypatch):
    reply = json.dumps(
        [
            {
                "audio_url": "https://music.example.org/files/Morning%20Light.mp3",
                "title": "Morning Light",
                "bpm": 92,
                "moods": ["Uplifting"],
                "genres": ["Acoustic"],
            },
            # Invented: not a URL on the page. Must be dropped, not imported.
            {"audio_url": "https://music.example.org/files/secret.mp3", "title": "Made up"},
        ]
    )
    provider = FakeProvider(f"Here you go:\n```json\n{reply}\n```")
    monkeypatch.setattr(page_import, "build_provider", lambda _s, _u: provider)

    body = library.post("/api/assets/import-page/preview", json={"url": PLAIN_URL}).json()["data"]

    assert body["method"] == "ai"
    assert [t["title"] for t in body["tracks"]] == ["Morning Light"]
    assert body["tracks"][0]["bpm"] == 92 and body["tracks"][0]["moods"] == ["Uplifting"]
    # The model was shown the page's own audio URLs to choose from.
    assert "https://cdn.example.org/tracks/night_drive.ogg" in provider.prompts[0]


def test_without_a_provider_the_bare_links_are_offered(library, pages, monkeypatch):
    monkeypatch.setattr(page_import, "build_provider", lambda _s, _u: None)
    body = library.post("/api/assets/import-page/preview", json={"url": PLAIN_URL}).json()["data"]
    assert body["method"] == "links" and len(body["tracks"]) == 3
    assert "No AI provider" in body["note"]


def test_structured_pages_never_cost_an_ai_call(library, pages, monkeypatch):
    def refuse(*_args):
        raise AssertionError("the AI was asked about a page with structured data")

    monkeypatch.setattr(page_import, "build_provider", refuse)
    body = library.post("/api/assets/import-page/preview", json={"url": MIXKIT_URL}).json()["data"]
    assert body["source"] == "mixkit" and len(body["tracks"]) == 2


def test_an_unreachable_page_is_a_readable_error(library, pages):
    response = library.post(
        "/api/assets/import-page/preview", json={"url": "https://music.example.org/gone"}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "page_unreadable"


# ─── importing ───────────────────────────────────────────────────────────────


@pytest.fixture
def downloads(monkeypatch):
    """A track download writes the sample MP3. Returns the URLs downloaded."""
    fetched: list[str] = []

    def download_audio(url, workdir, stem, **_kwargs):
        fetched.append(url)
        target = Path(workdir) / f"{stem}.mp3"
        shutil.copy(SAMPLE_MP3, target)
        return target

    monkeypatch.setattr(web, "download_audio", download_audio)
    return fetched


def _run_job(job_id, session, monkeypatch):
    from app.jobs import enrichment as enrichment_jobs

    monkeypatch.setattr(enrichment_jobs.queue(), "engine", session.get_bind())
    enrichment_jobs._run_job(job_id)
    session.expire_all()


def test_a_chosen_track_is_imported_with_what_its_page_said(
    library, session, pages, downloads, monkeypatch
):
    preview = library.post("/api/assets/import-page/preview", json={"url": MIXKIT_URL}).json()
    chosen = preview["data"]["tracks"][0]

    response = library.post("/api/assets/import-page", json={"tracks": [chosen]})
    assert response.status_code == 202
    [job] = response.json()["data"]
    assert job["action"] == KIND_IMPORT_TRACK and job["asset_name"] == "I'm Still Waitin'"

    _run_job(job["id"], session, monkeypatch)

    assert downloads == ["https://assets.mixkit.co/music/910/910.mp3"]
    finished = session.get(EnrichmentJob, job["id"])
    assert finished.status == "done", finished.error_message
    asset = session.exec(select(Asset).where(Asset.name == "I'm Still Waitin'")).one()
    assert asset.asset_type == "audio"
    assert asset.creator == "Michael Ramir C." and asset.publisher == "Mixkit"
    assert asset.source_url == "https://assets.mixkit.co/music/910/910.mp3"
    assert asset.retrieved_at is not None
    assert music.decode_terms(asset.music_moods) == ["Warm", "Carefree"]
    assert music.decode_terms(asset.music_instruments) == ["Acoustic Guitar"]
    assert json.loads(asset.field_provenance)["music_genres"] == PROVENANCE_EMBEDDED
    # The site's tags become library tags, and the job says what it made.
    names = [t["name"] for t in library.get(f"/api/assets/{asset.id}").json()["data"]["tags"]]
    assert names == ["Road Trip"]
    assert library.get("/api/activity").json()["data"][0]["result_asset_id"] == asset.id


def test_a_track_already_in_the_library_is_not_downloaded_twice(
    library, session, pages, downloads, monkeypatch
):
    chosen = library.post(
        "/api/assets/import-page/preview", json={"url": MIXKIT_URL}
    ).json()["data"]["tracks"][0]
    first = library.post("/api/assets/import-page", json={"tracks": [chosen]}).json()["data"][0]
    _run_job(first["id"], session, monkeypatch)

    # The review list says so...
    again = library.post("/api/assets/import-page/preview", json={"url": MIXKIT_URL}).json()
    assert again["data"]["tracks"][0]["existing_asset_id"] is not None
    # ...and an import anyway finishes pointing at the copy already there.
    second = library.post("/api/assets/import-page", json={"tracks": [chosen]}).json()["data"][0]
    _run_job(second["id"], session, monkeypatch)
    assert len(downloads) == 1
    assert session.get(EnrichmentJob, second["id"]).detail == "Already in your library"


def test_the_same_file_from_another_url_is_not_imported_twice(
    library, session, downloads, monkeypatch
):
    """A mirror, or a second link to one file: the URL differs, the bytes do not."""
    first = {"audio_url": "https://a.example.org/song.mp3", "title": "Song"}
    second = {
        "audio_url": "https://mirror.example.net/other-name.mp3",
        "title": "Song (mirror)",
        "creator": "Jane Doe",
        "moods": ["Calm"],
    }
    job = library.post("/api/assets/import-page", json={"tracks": [first]}).json()["data"][0]
    _run_job(job["id"], session, monkeypatch)
    job = library.post("/api/assets/import-page", json={"tracks": [second]}).json()["data"][0]
    _run_job(job["id"], session, monkeypatch)

    [asset] = session.exec(select(Asset).where(Asset.asset_type == "audio")).all()
    finished = session.get(EnrichmentJob, job["id"])
    assert finished.status == "done"
    assert finished.detail == "Already in your library as \u201cSong\u201d (the same file)"
    # The page's details fill the copy's blanks, and the job points at it.
    assert asset.creator == "Jane Doe" and music.decode_terms(asset.music_moods) == ["Calm"]
    assert library.get("/api/activity").json()["data"][0]["result_asset_id"] == asset.id


def test_a_file_uploaded_by_hand_is_recognised_and_keeps_its_corrections(
    library, session, downloads, monkeypatch
):
    files = [("files", ("mine.mp3", SAMPLE_MP3.read_bytes(), "audio/mpeg"))]
    mine = library.post("/api/assets", files=files).json()["created"][0]
    library.patch(f"/api/assets/{mine['id']}", json={"creator": "Who I say it is"})

    track = {"audio_url": "https://a.example.org/x.mp3", "title": "X", "creator": "The site"}
    job = library.post("/api/assets/import-page", json={"tracks": [track]}).json()["data"][0]
    _run_job(job["id"], session, monkeypatch)

    assert len(session.exec(select(Asset).where(Asset.asset_type == "audio")).all()) == 1
    stored = session.get(Asset, mine["id"])
    assert stored.creator == "Who I say it is"
    # A blank is still filled, so the next import recognises it by URL alone.
    assert stored.source_url == "https://a.example.org/x.mp3"


def test_the_same_track_ticked_twice_is_one_job(library, session):
    track = {"audio_url": "https://a.example.org/song.mp3", "title": "Song"}
    jobs = library.post("/api/assets/import-page", json={"tracks": [track, track]}).json()["data"]
    assert len(jobs) == 1


def test_a_track_with_a_familiar_title_is_flagged_in_the_review(library, session, pages, monkeypatch):
    """Same piece, different file (downloaded elsewhere): only its name can tell."""
    files = [("files", ("x.mp3", SAMPLE_MP3.read_bytes(), "audio/mpeg"))]
    mine = library.post("/api/assets", files=files).json()["created"][0]
    library.patch(f"/api/assets/{mine['id']}", json={"name": "Bigger Than Texas"})

    tracks = library.post(
        "/api/assets/import-page/preview", json={"url": MIXKIT_URL}
    ).json()["data"]["tracks"]
    by_title = {t["title"]: t for t in tracks}
    assert by_title["Bigger than Texas"]["similar_asset_id"] == mine["id"]
    assert by_title["Bigger than Texas"]["existing_asset_id"] is None
    assert by_title["I'm Still Waitin'"]["similar_asset_id"] is None


def test_a_familiar_title_by_another_artist_is_not_flagged(library, session, pages):
    files = [("files", ("x.mp3", SAMPLE_MP3.read_bytes(), "audio/mpeg"))]
    mine = library.post("/api/assets", files=files).json()["created"][0]
    library.patch(
        f"/api/assets/{mine['id']}", json={"name": "Bigger than Texas", "creator": "Someone Else"}
    )
    tracks = library.post(
        "/api/assets/import-page/preview", json={"url": MIXKIT_URL}
    ).json()["data"]["tracks"]
    assert all(t["similar_asset_id"] is None for t in tracks)


def test_a_date_from_the_browser_is_normalised_not_trusted(library, session, downloads, monkeypatch):
    track = {"audio_url": "https://music.example.org/a.mp3", "title": "A", "published_date": "summer"}
    job = library.post("/api/assets/import-page", json={"tracks": [track]}).json()["data"][0]
    _run_job(job["id"], session, monkeypatch)
    assert session.exec(select(Asset).where(Asset.name == "A")).one().published_date is None


def test_an_import_refuses_an_inward_address(library):
    track = {"audio_url": "https://127.0.0.1/track.mp3", "title": "Sneaky"}
    response = library.post("/api/assets/import-page", json={"tracks": [track]})
    assert response.status_code == 400


def test_too_many_tracks_at_once_are_refused(library):
    tracks = [
        {"audio_url": f"https://music.example.org/{i}.mp3", "title": f"T{i}"}
        for i in range(page_import.MAX_TRACKS_PER_IMPORT + 1)
    ]
    assert library.post("/api/assets/import-page", json={"tracks": tracks}).status_code == 422


# ─── the fetcher ─────────────────────────────────────────────────────────────


def _serve(monkeypatch, handler):
    """Route `web`'s requests to `handler` instead of the network."""
    def client(accept_encoding):
        return httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            headers={"Accept-Encoding": accept_encoding},
        )

    monkeypatch.setattr(web, "_client", client)


def test_a_redirect_inward_is_refused(monkeypatch, tmp_path):
    def handler(request):
        return httpx.Response(302, headers={"location": "https://10.0.0.5/secret.mp3"})

    _serve(monkeypatch, handler)
    with pytest.raises(web.WebFetchError, match="not one this app will fetch"):
        web.download_audio("https://music.example.org/t.mp3", tmp_path, "t")


def test_a_link_that_answers_with_a_page_is_not_a_track(monkeypatch, tmp_path):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")

    _serve(monkeypatch, handler)
    with pytest.raises(web.WebFetchError, match="not an audio file"):
        web.download_audio("https://music.example.org/download?id=4", tmp_path, "t")
    assert list(tmp_path.iterdir()) == []


def test_a_track_over_the_cap_is_refused_and_removed(monkeypatch, tmp_path):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"x" * 2048)

    _serve(monkeypatch, handler)
    with pytest.raises(web.WebFetchError, match="larger than"):
        web.download_audio("https://music.example.org/t.mp3", tmp_path, "t", max_bytes=1024)
    assert list(tmp_path.iterdir()) == []


def test_a_track_downloads_with_its_type_from_the_response(monkeypatch, tmp_path):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "audio/ogg"}, content=b"OggS...")

    _serve(monkeypatch, handler)
    path = web.download_audio("https://music.example.org/stream?id=4", tmp_path, "t")
    assert path.name == "t.ogg" and path.read_bytes() == b"OggS..."


def test_a_page_is_read_through_its_redirect(monkeypatch):
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>hi</p>")

    _serve(monkeypatch, handler)
    final, text = web.fetch_text("https://music.example.org/old")
    assert final == "https://music.example.org/new" and text == "<p>hi</p>"
