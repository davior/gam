"""The import_url job, end to end, with yt-dlp replaced and everything else real.

`FakeYoutubeDL` stands in for the network: it answers `extract_info` from a table of
info dicts and, when asked to download, writes real fixture media where yt-dlp would.
So ingest, ffprobe, thumbnails, clipping, the transcript writer and the keyword index
all run for real — the only thing assumed is what the site says. That assumption is
also the one thing this suite cannot check, since no CI run should depend on YouTube;
the options passed to yt-dlp are asserted on instead, which is as close as a hermetic
test gets.
"""

import copy
import json
import shutil
import socket
from pathlib import Path

import pytest
from sqlmodel import col, select
from yt_dlp.utils import DownloadError

from app.ingest import ytdlp
from app.jobs import enrichment as enrichment_jobs
from app.jobs.runner import JobCancelled
from app.media_tools import ffmpeg_available
from app.models.asset import Asset
from app.models.job import KIND_EMBED, KIND_IMPORT_URL, KIND_TRANSCRIBE, EnrichmentJob
from app.models.suggestion import KIND_TAG, Suggestion
from app.models.transcript import TranscriptSegment
from app.embeddings import PROVIDER_OLLAMA
from app.services import tags as tag_service
from app.settings_store import DEEPGRAM_API_KEY, EMBEDDING_PROVIDER, set_setting

FIXTURES = Path(__file__).parent / "fixtures"
TEST_USER = "user-under-test"
VIDEO_URL = "https://www.youtube.com/watch?v=abc123"

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")

CAPTIONS_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:01.000
We are talking about nano weapons.

00:00:01.000 --> 00:00:02.000
And their deployment.
"""


def video_info(**overrides):
    """What yt-dlp says about one YouTube video, trimmed to the keys the importer reads."""
    info = {
        "_type": "video",
        "id": "abc123",
        "title": "Giordano on neuroweapons",
        "description": "A lecture at the Naval War College.\nhttps://example.org/notes",
        "webpage_url": VIDEO_URL,
        "channel": "Lecture Archive",
        "uploader": "Lecture Archive",
        "upload_date": "20171205",
        "license": "Creative Commons Attribution license (reuse allowed)",
        "tags": ["Neuroscience", "  national   security ", "neuroscience", "x" * 90],
        "chapters": [
            {"start_time": 0.0, "end_time": 1.0, "title": "Opening"},
            # Past the 2s fixture's end: must be clamped, not refused.
            {"start_time": 1.0, "end_time": 3.0, "title": "Discussion"},
        ],
        "language": "en",
        "subtitles": {},
        "automatic_captions": {"en-orig": [{"ext": "vtt"}], "fr": [{"ext": "vtt"}]},
        "extractor_key": "Youtube",
        "live_status": "not_live",
    }
    info.update(overrides)
    return info


class FakeYoutubeDL:
    """Answers from `infos`; records every construction in `made`."""

    infos: dict = {}
    made: list = []
    # When set, raised from `extract_info` for the matching URL.
    errors: dict = {}

    def __init__(self, params):
        self.params = params
        FakeYoutubeDL.made.append(params)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if url in FakeYoutubeDL.errors:
            raise FakeYoutubeDL.errors[url]
        info = copy.deepcopy(FakeYoutubeDL.infos[url])
        if not download:
            return info

        workdir = Path(self.params["paths"]["home"])
        for hook in self.params.get("progress_hooks", []):
            hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 50,
                    "total_bytes": 100,
                    "info_dict": {"vcodec": "avc1"},
                }
            )

        audio_only = self.params.get("format", "").startswith("ba")
        if audio_only:
            media = workdir / "media.m4a"
            shutil.copyfile(FIXTURES / "sample_audio.mp3", media)
        else:
            media = workdir / "media.mp4"
            shutil.copyfile(FIXTURES / "sample_video.mp4", media)
        shutil.copyfile(FIXTURES / "sample_image.jpg", workdir / "thumb.jpg")

        for lang in self.params.get("subtitleslangs") or []:
            (workdir / f"captions.{lang}.vtt").write_text(CAPTIONS_VTT)

        info["requested_downloads"] = [{"filepath": str(media)}]
        return info


@pytest.fixture(autouse=True)
def fake_ytdlp(monkeypatch):
    FakeYoutubeDL.infos = {VIDEO_URL: video_info()}
    FakeYoutubeDL.made = []
    FakeYoutubeDL.errors = {}
    monkeypatch.setattr(ytdlp.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    # Every name resolves somewhere public, so the SSRF guard never touches real DNS.
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )
    return FakeYoutubeDL


def _import(library, session, monkeypatch, url=VIDEO_URL, **options):
    """Queue through the real endpoint, then run the worker on it directly."""
    response = library.post("/api/assets/import", json={"url": url, **options})
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["id"]
    _run(session, monkeypatch, job_id)
    return job_id


def _run(session, monkeypatch, job_id):
    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job_id)
    session.expire_all()
    return session.get(EnrichmentJob, job_id)


def _activity(library, job_id):
    return library.get(f"/api/activity/enrichment/{job_id}").json()["data"]


def _only_import(session):
    rows = session.exec(select(Asset).where(Asset.source == "url")).all()
    assert len(rows) == 1
    return rows[0]


# ─── the happy path ──────────────────────────────────────────────────────────


@needs_ffmpeg
def test_a_video_is_imported_with_what_the_site_said_about_it(library, session, monkeypatch):
    job_id = _import(library, session, monkeypatch)

    job = _activity(library, job_id)
    assert job["status"] == "done", job["error_message"]
    asset = _only_import(session)
    assert job["result_asset_id"] == asset.id
    assert job["asset_name"] == "Giordano on neuroweapons"

    assert asset.name == "Giordano on neuroweapons"
    assert asset.asset_type == "video"
    assert asset.source == "url"
    assert asset.original_name == "Giordano on neuroweapons.mp4"
    assert asset.duration_seconds == pytest.approx(2.0, abs=0.2)
    assert asset.description.startswith("A lecture at the Naval War College.")

    assert asset.source_url == VIDEO_URL
    assert asset.publisher == "Lecture Archive"
    assert asset.source_title == "Giordano on neuroweapons"
    assert asset.published_date == "2017-12-05"
    assert asset.license == "Creative Commons Attribution license (reuse allowed)"
    assert asset.retrieved_at is not None
    # The channel is the outlet, not the speaker: a creator copied from it would be
    # the confidently wrong citation M10 exists to prevent.
    assert asset.creator is None


@needs_ffmpeg
def test_site_metadata_is_stamped_embedded_not_human(library, session, monkeypatch):
    _import(library, session, monkeypatch)
    provenance = json.loads(_only_import(session).field_provenance)

    for name in ("description", "publisher", "source_url", "published_date", "retrieved_at"):
        assert provenance[name] == "embedded"


@needs_ffmpeg
def test_the_finished_asset_is_readable_through_the_api(library, session, monkeypatch):
    job_id = _import(library, session, monkeypatch)
    asset_id = _activity(library, job_id)["result_asset_id"]

    body = library.get(f"/api/assets/{asset_id}").json()["data"]
    assert body["source"] == "url"
    assert body["thumb_url"]
    assert body["credit"] == (
        "Giordano on neuroweapons — Lecture Archive — 2017-12-05 — "
        "Creative Commons Attribution license (reuse allowed)"
    )


@needs_ffmpeg
def test_the_import_is_findable_by_its_channel(library, session, monkeypatch):
    _import(library, session, monkeypatch)
    listing = library.get("/api/assets", params={"publisher": "lecture archive"}).json()
    # The parent and both chapter clips, which inherit its publisher.
    assert listing["total"] == 3


# ─── what gets passed to yt-dlp ──────────────────────────────────────────────


@needs_ffmpeg
def test_named_sites_only_and_the_quality_cap(library, session, monkeypatch):
    _import(library, session, monkeypatch)

    for params in FakeYoutubeDL.made:
        assert params["allowed_extractors"] == ["default", "-generic"]
        assert params["noplaylist"] is True
        assert params["ignoreerrors"] is False

    download = FakeYoutubeDL.made[-1]
    assert download["format"] == "bv*+ba/b"
    assert download["format_sort"] == ["res:1080", "vcodec:h264", "acodec:m4a"]
    assert download["merge_output_format"] == "mp4"
    # Fixed output names: a filename built from the title is built from text the site
    # controls.
    assert download["outtmpl"]["default"] == "media.%(ext)s"


@needs_ffmpeg
def test_the_quality_cap_follows_the_setting(library, session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "url_import_max_height", 720)
    _import(library, session, monkeypatch)
    assert FakeYoutubeDL.made[-1]["format_sort"][0] == "res:720"


@needs_ffmpeg
def test_a_cookies_file_is_copied_not_used_in_place(library, session, monkeypatch, tmp_path):
    """yt-dlp writes the jar back out on close, which fails on the read-only mount the
    docs recommend — and would otherwise rewrite the user's own file."""
    from app.config import settings

    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(settings, "url_import_cookies_file", str(cookies))

    _import(library, session, monkeypatch)

    used = FakeYoutubeDL.made[-1]["cookiefile"]
    assert used != str(cookies)
    assert Path(used).name == "cookies.txt"


def test_a_missing_cookies_file_fails_with_a_message(library, session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "url_import_cookies_file", "/nowhere/cookies.txt")
    job_id = _import(library, session, monkeypatch)

    job = _activity(library, job_id)
    assert job["status"] == "error"
    assert "URL_IMPORT_COOKIES_FILE" in job["error_message"]


# ─── tags ────────────────────────────────────────────────────────────────────


@needs_ffmpeg
def test_uploader_tags_are_applied_by_default(library, session, monkeypatch):
    _import(library, session, monkeypatch)
    asset = _only_import(session)

    names = sorted(t.name for t in tag_service.tags_for(session, asset.id))
    # Whitespace collapsed, the case-insensitive duplicate dropped, and the 90-character
    # one left out as longer than any tag may be.
    assert names == ["Neuroscience", "national security"]


@needs_ffmpeg
def test_applied_tags_are_searchable(library, session, monkeypatch):
    _import(library, session, monkeypatch)
    listing = library.get("/api/assets", params={"tag": "national security"}).json()
    assert listing["total"] == 1


@needs_ffmpeg
def test_tags_can_be_offered_as_suggestions_instead(library, session, monkeypatch):
    _import(library, session, monkeypatch, apply_tags=False)
    asset = _only_import(session)

    assert tag_service.tags_for(session, asset.id) == []
    pending = session.exec(
        select(Suggestion).where(Suggestion.asset_id == asset.id, Suggestion.kind == KIND_TAG)
    ).all()
    assert sorted(s.value for s in pending) == ["Neuroscience", "national security"]
    assert all(s.model == "youtube tags" for s in pending)


# ─── chapters ────────────────────────────────────────────────────────────────


@needs_ffmpeg
def test_chapters_become_clips(library, session, monkeypatch):
    _import(library, session, monkeypatch)
    parent = _only_import(session)

    clips = session.exec(
        select(Asset).where(Asset.parent_asset_id == parent.id).order_by(col(Asset.in_point))
    ).all()
    assert [c.name for c in clips] == [
        "Opening — Giordano on neuroweapons",
        "Discussion — Giordano on neuroweapons",
    ]
    assert all(c.source == "clip" and c.storage_key is None for c in clips)
    assert (clips[0].in_point, clips[0].out_point) == (0.0, 1.0)
    # Clamped to the file's real length rather than refused for running past it.
    assert clips[1].out_point == pytest.approx(parent.duration_seconds)


@needs_ffmpeg
def test_chapter_clips_can_be_turned_off(library, session, monkeypatch):
    _import(library, session, monkeypatch, chapters_as_clips=False)
    parent = _only_import(session)
    assert session.exec(select(Asset).where(Asset.parent_asset_id == parent.id)).all() == []


@needs_ffmpeg
def test_a_single_chapter_is_not_a_clip(library, session, monkeypatch):
    """One chapter spanning the video would be a clip identical to its parent."""
    FakeYoutubeDL.infos[VIDEO_URL] = video_info(
        chapters=[{"start_time": 0.0, "end_time": 2.0, "title": "Everything"}]
    )
    _import(library, session, monkeypatch)
    parent = _only_import(session)
    assert session.exec(select(Asset).where(Asset.parent_asset_id == parent.id)).all() == []


# ─── captions and transcription ──────────────────────────────────────────────


@needs_ffmpeg
def test_without_deepgram_the_captions_become_the_transcript(library, session, monkeypatch):
    job_id = _import(library, session, monkeypatch)
    asset = _only_import(session)

    # The original-language speech track, not the French auto-translation beside it.
    assert FakeYoutubeDL.made[-1]["subtitleslangs"] == ["en-orig"]
    assert FakeYoutubeDL.made[-1]["writeautomaticsub"] is True

    segments = session.exec(
        select(TranscriptSegment)
        .where(TranscriptSegment.asset_id == asset.id)
        .order_by(col(TranscriptSegment.idx))
    ).all()
    assert [s.text for s in segments] == [
        "We are talking about nano weapons.",
        "And their deployment.",
    ]
    assert asset.transcript_status == "done"
    assert asset.transcript_model == "youtube-auto-captions"
    assert asset.transcript_language == "en"
    assert "captions as transcript" in _activity(library, job_id)["detail"]


@needs_ffmpeg
def test_caption_text_is_keyword_searchable(library, session, monkeypatch):
    _import(library, session, monkeypatch)
    results = library.get("/api/search", params={"q": "nano weapons"}).json()["data"]
    assert results and results[0]["start_time"] == 0.0


@needs_ffmpeg
def test_captions_chain_an_embedding_like_a_transcript_does(library, session, monkeypatch):
    set_setting(session, TEST_USER, EMBEDDING_PROVIDER, PROVIDER_OLLAMA)
    _import(library, session, monkeypatch)
    asset = _only_import(session)

    embeds = session.exec(
        select(EnrichmentJob).where(
            EnrichmentJob.asset_id == asset.id, EnrichmentJob.kind == KIND_EMBED
        )
    ).all()
    assert len(embeds) == 1


@needs_ffmpeg
def test_hand_written_captions_are_preferred(library, session, monkeypatch):
    FakeYoutubeDL.infos[VIDEO_URL] = video_info(subtitles={"en-GB": [{"ext": "vtt"}]})
    _import(library, session, monkeypatch)

    download = FakeYoutubeDL.made[-1]
    assert download["subtitleslangs"] == ["en-GB"]
    assert download["writesubtitles"] is True
    assert _only_import(session).transcript_model == "youtube-captions"


@needs_ffmpeg
def test_with_deepgram_the_captions_are_not_fetched(library, session, monkeypatch):
    """The user's rule: Deepgram when there is a key. Its transcript is diarised and
    word-timed, and the upload chain queues it on its own."""
    set_setting(session, TEST_USER, DEEPGRAM_API_KEY, "dg-test-key")
    _import(library, session, monkeypatch)
    asset = _only_import(session)

    assert "subtitleslangs" not in FakeYoutubeDL.made[-1]
    assert session.exec(
        select(TranscriptSegment).where(TranscriptSegment.asset_id == asset.id)
    ).all() == []
    transcribe = session.exec(
        select(EnrichmentJob).where(
            EnrichmentJob.asset_id == asset.id, EnrichmentJob.kind == KIND_TRANSCRIBE
        )
    ).all()
    assert len(transcribe) == 1


@needs_ffmpeg
def test_no_captions_at_all_is_not_a_failure(library, session, monkeypatch):
    FakeYoutubeDL.infos[VIDEO_URL] = video_info(automatic_captions={}, subtitles={})
    job_id = _import(library, session, monkeypatch)

    assert _activity(library, job_id)["status"] == "done"
    assert _only_import(session).transcript_status is None


# ─── audio only ──────────────────────────────────────────────────────────────


@needs_ffmpeg
def test_audio_only_imports_an_audio_asset(library, session, monkeypatch):
    _import(library, session, monkeypatch, audio_only=True)
    asset = _only_import(session)

    download = FakeYoutubeDL.made[-1]
    assert download["format"].startswith("ba")
    assert download["postprocessors"][0] == {
        "key": "FFmpegExtractAudio",
        "preferredcodec": "m4a",
    }
    assert asset.asset_type == "audio"
    assert asset.file_format == "m4a"
    # The site's thumbnail, which is the only picture an audio asset gets.
    assert asset.thumb_key


# ─── duplicates ──────────────────────────────────────────────────────────────


@needs_ffmpeg
def test_importing_the_same_video_twice_points_at_the_first(library, session, monkeypatch):
    first = _activity(library, _import(library, session, monkeypatch))["result_asset_id"]
    downloads_before = sum(1 for p in FakeYoutubeDL.made if "paths" in p)

    job_id = _import(library, session, monkeypatch)
    job = _activity(library, job_id)

    assert job["status"] == "done"
    assert job["detail"] == "Already in your library"
    assert job["result_asset_id"] == first
    assert sum(1 for p in FakeYoutubeDL.made if "paths" in p) == downloads_before
    assert len(session.exec(select(Asset).where(Asset.source == "url")).all()) == 1


# ─── playlists ───────────────────────────────────────────────────────────────

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL123"


def _playlist(*entries, **overrides):
    info = {
        "_type": "playlist",
        "title": "Conference 2017",
        "entries": [{"_type": "url", "url": url, "title": title} for url, title in entries],
    }
    info.update(overrides)
    return info


def _children(session, parent_id):
    """Imports a playlist queued — the ones whose payload says they came from one."""
    return [
        job
        for job in session.exec(
            select(EnrichmentJob)
            .where(EnrichmentJob.kind == KIND_IMPORT_URL)
            .order_by(col(EnrichmentJob.created_at))
        ).all()
        if job.id != parent_id and json.loads(job.payload).get("depth", 0) >= 1
    ]


def test_a_playlist_queues_one_import_per_video(library, session, monkeypatch):
    FakeYoutubeDL.infos[PLAYLIST_URL] = _playlist(
        ("https://www.youtube.com/watch?v=one", "Day one"),
        ("https://www.youtube.com/watch?v=two", "Day two"),
        ("not-a-url", "Skipped: no absolute URL"),
    )
    job_id = _import(library, session, monkeypatch, url=PLAYLIST_URL, audio_only=True)

    job = _activity(library, job_id)
    assert job["status"] == "done"
    assert job["detail"] == "Queued 2 imports"
    assert job["asset_name"] == "Conference 2017"

    children = _children(session, job_id)
    assert [c.asset_name for c in children] == ["Day one", "Day two"]
    for child in children:
        payload = json.loads(child.payload)
        assert payload["audio_only"] is True
        assert payload["depth"] == 1
        assert child.status == "queued"


def test_a_playlist_is_capped(library, session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "url_import_max_playlist_items", 2)
    FakeYoutubeDL.infos[PLAYLIST_URL] = _playlist(
        ("https://www.youtube.com/watch?v=one", "One"),
        ("https://www.youtube.com/watch?v=two", "Two"),
        playlist_count=40,
    )
    job_id = _import(library, session, monkeypatch, url=PLAYLIST_URL)

    assert FakeYoutubeDL.made[0]["playlist_items"] == "1:2"
    assert _activity(library, job_id)["detail"] == "Queued 2 imports (the first 2 of 40)"


@needs_ffmpeg
def test_a_playlist_skips_what_is_already_imported(library, session, monkeypatch):
    _import(library, session, monkeypatch)  # VIDEO_URL is now in the library
    FakeYoutubeDL.infos[PLAYLIST_URL] = _playlist(
        (VIDEO_URL, "Already here"),
        ("https://www.youtube.com/watch?v=new", "New"),
    )
    job_id = _import(library, session, monkeypatch, url=PLAYLIST_URL)

    assert _activity(library, job_id)["detail"] == "Queued 1 import, 1 already in your library"
    assert [c.asset_name for c in _children(session, job_id)] == ["New"]


@needs_ffmpeg
def test_a_queued_playlist_entry_imports_like_any_other(library, session, monkeypatch):
    FakeYoutubeDL.infos[PLAYLIST_URL] = _playlist((VIDEO_URL, "Only video"))
    parent_id = _import(library, session, monkeypatch, url=PLAYLIST_URL)
    [child] = _children(session, parent_id)

    _run(session, monkeypatch, child.id)
    assert _activity(library, child.id)["status"] == "done"
    assert _only_import(session).source_url == VIDEO_URL


def test_playlists_nested_too_deep_are_refused(library, session, monkeypatch):
    FakeYoutubeDL.infos[PLAYLIST_URL] = _playlist(("https://www.youtube.com/watch?v=x", "x"))
    job = EnrichmentJob(
        user_id=TEST_USER,
        kind=KIND_IMPORT_URL,
        payload=json.dumps({"url": PLAYLIST_URL, "depth": 2}),
    )
    session.add(job)
    session.commit()

    finished = _run(session, monkeypatch, job.id)
    assert finished.status == "error"
    assert "nested too deeply" in finished.error_message


def test_an_empty_playlist_is_an_error(library, session, monkeypatch):
    FakeYoutubeDL.infos[PLAYLIST_URL] = _playlist()
    job = _activity(library, _import(library, session, monkeypatch, url=PLAYLIST_URL))
    assert job["status"] == "error"
    assert "nothing in it" in job["error_message"]


# ─── refusals and failures ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("live_status", "fragment"),
    [("is_live", "live stream"), ("is_upcoming", "not started"), ("post_live", "just ended")],
)
def test_live_streams_are_refused(library, session, monkeypatch, live_status, fragment):
    FakeYoutubeDL.infos[VIDEO_URL] = video_info(live_status=live_status)
    job = _activity(library, _import(library, session, monkeypatch))

    assert job["status"] == "error"
    assert fragment in job["error_message"]
    assert session.exec(select(Asset)).all() == []


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "ERROR: [youtube] abc123: Sign in to confirm you’re not a bot. Use --cookies-from-browser",
            "URL_IMPORT_COOKIES_FILE",
        ),
        (
            "ERROR: No suitable extractor found for URL https://example.com/x",
            "not one yt-dlp knows",
        ),
        (
            "ERROR: [youtube] abc123: Requested format is not available",
            "yt-dlp needs updating",
        ),
        ("ERROR: [youtube] abc123: Private video", "Private video"),
    ],
)
def test_yt_dlp_failures_become_readable_messages(library, session, monkeypatch, message, expected):
    FakeYoutubeDL.errors[VIDEO_URL] = DownloadError(message)
    job = _activity(library, _import(library, session, monkeypatch))

    assert job["status"] == "error"
    assert expected in job["error_message"]
    assert not job["error_message"].startswith("ERROR")


def test_cancelling_mid_download_stops_the_job(library, session, monkeypatch):
    """The progress hook is the checkpoint: the reporter raises JobCancelled, which the
    hook turns into the exception yt-dlp is written to let through."""

    def cancelled_reporter(self, job_id):
        def report(stage, percent, detail=""):
            if stage.startswith("Downloading"):
                raise JobCancelled(job_id)

        return report

    monkeypatch.setattr(type(enrichment_jobs.queue()), "reporter", cancelled_reporter)
    job_id = _import(library, session, monkeypatch)

    assert _activity(library, job_id)["status"] == "cancelled"
    assert session.exec(select(Asset)).all() == []


def test_a_malformed_payload_fails_cleanly(library, session, monkeypatch):
    job = EnrichmentJob(user_id=TEST_USER, kind=KIND_IMPORT_URL, payload="{not json")
    session.add(job)
    session.commit()

    finished = _run(session, monkeypatch, job.id)
    assert finished.status == "error"
    assert "no link" in finished.error_message


def test_a_child_url_pointing_inward_is_refused_by_the_job(library, session, monkeypatch):
    """A playlist's entries are URLs the site chose. The job re-checks them rather than
    trusting that the router saw them — it never did."""
    job = EnrichmentJob(
        user_id=TEST_USER,
        kind=KIND_IMPORT_URL,
        payload=json.dumps({"url": "https://127.0.0.1/admin", "depth": 1}),
    )
    session.add(job)
    session.commit()

    finished = _run(session, monkeypatch, job.id)
    assert finished.status == "error"
    assert "public host" in finished.error_message
    assert FakeYoutubeDL.made == []


# ─── the pure mapping ────────────────────────────────────────────────────────


def test_creator_is_only_taken_when_the_site_names_one():
    meta = ytdlp.metadata_from_info(video_info(creators=["Jane Doe", "John Roe"]))
    assert meta.attribution["creator"] == "Jane Doe, John Roe"


def test_series_is_the_containing_work_when_there_is_one():
    meta = ytdlp.metadata_from_info(video_info(series="The Long Interview"))
    assert meta.attribution["source_title"] == "The Long Interview"
    assert meta.title == "Giordano on neuroweapons"


def test_no_license_is_invented():
    meta = ytdlp.metadata_from_info(video_info(license=None))
    assert "license" not in meta.attribution


def test_release_date_wins_over_upload_date():
    meta = ytdlp.metadata_from_info(video_info(release_date="20170101"))
    assert meta.attribution["published_date"] == "2017-01-01"


def test_a_bare_release_year_is_kept_as_a_year():
    meta = ytdlp.metadata_from_info(video_info(upload_date=None, release_year=1994))
    assert meta.attribution["published_date"] == "1994"
