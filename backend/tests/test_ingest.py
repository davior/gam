"""Probing and thumbnailing, against real media rather than mocks.

The fixtures in tests/fixtures are genuine files built by tests/make_fixtures.py. A
mocked ffprobe would only prove that the parsing matches whatever fixture JSON was
written by hand — which is exactly the part most likely to be wrong.
"""

import io
from pathlib import Path

import pytest
from PIL import Image

from app.ingest import thumbnails
from app.ingest.filetypes import (
    TYPE_AUDIO,
    TYPE_DOCUMENT,
    TYPE_IMAGE,
    TYPE_VIDEO,
    asset_type_for,
    display_name_from,
    is_allowed,
    sanitize_original_name,
)
from app.ingest.probe import probe
from app.media_tools import ffmpeg_available

FIXTURES = Path(__file__).parent / "fixtures"

needs_ffmpeg = pytest.mark.skipif(
    not ffmpeg_available(), reason="ffmpeg/ffprobe not on PATH"
)


# ─── taxonomy ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("photo.JPG", TYPE_IMAGE),
        ("clip.mp4", TYPE_VIDEO),
        ("Interview.MOV", TYPE_VIDEO),
        ("song.mp3", TYPE_AUDIO),
        ("paper.pdf", TYPE_DOCUMENT),
        ("notes.md", TYPE_DOCUMENT),
        ("archive.zip", None),
        ("script.sh", None),
        ("noextension", None),
    ],
)
def test_asset_type_detection(filename, expected):
    assert asset_type_for(filename) == expected
    assert is_allowed(filename) is (expected is not None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("holiday.jpg", "holiday.jpg"),
        ("/etc/passwd", "passwd"),
        ("../../escape.jpg", "escape.jpg"),
        ("with\nnewline.jpg", "withnewline.jpg"),
        ("null\x00byte.jpg", "nullbyte.jpg"),
        ("", None),
        (None, None),
    ],
)
def test_original_name_is_sanitised(raw, expected):
    """It never reaches the filesystem, but it is displayed — so it is still hostile
    text and must not carry control characters or path separators."""
    assert sanitize_original_name(raw) == expected


def test_original_name_is_length_capped():
    assert len(sanitize_original_name("a" * 500)) == 255


def test_display_name_drops_the_extension():
    """Ingestion needs only a file, so something must fill the name field, and
    "beach_sunset" beats a UUID."""
    assert display_name_from("beach_sunset.jpg") == "beach_sunset"
    assert display_name_from("no-extension") == "no-extension"
    assert display_name_from(None) == "Untitled"
    assert display_name_from("") == "Untitled"
    assert display_name_from(".gitignore") == ".gitignore"


# ─── probing ─────────────────────────────────────────────────────────────────


@needs_ffmpeg
def test_probe_video():
    result = probe(FIXTURES / "sample_video.mp4")

    assert result.has_video is True
    assert result.has_audio is True
    assert result.width == 320
    assert result.height == 240
    assert result.codec == "h264"
    assert result.duration_seconds == pytest.approx(2.0, abs=0.3)


@needs_ffmpeg
def test_probe_reports_rotated_video_as_displayed():
    """Phone footage carries rotation in metadata, not pixels. A portrait clip filed
    as landscape would sort and display wrongly everywhere."""
    result = probe(FIXTURES / "rotated_video.mp4")

    # Coded 320x240; rotated 90° it is displayed 240x320.
    assert (result.width, result.height) == (240, 320)


@needs_ffmpeg
def test_probe_audio_has_duration_but_no_dimensions():
    result = probe(FIXTURES / "sample_audio.mp3")

    assert result.has_audio is True
    assert result.has_video is False
    assert result.duration_seconds == pytest.approx(2.0, abs=0.3)
    assert result.width is None
    assert result.height is None


def test_probe_of_a_non_media_file_is_empty_not_an_error(tmp_path):
    """A file that will not probe is still a perfectly good asset."""
    junk = tmp_path / "notes.txt"
    junk.write_text("this is not media")

    result = probe(junk)
    assert result.duration_seconds is None
    assert result.has_video is False


def test_probe_of_a_missing_file_is_empty_not_an_error(tmp_path):
    assert probe(tmp_path / "nothing-here.mp4").duration_seconds is None


# ─── thumbnails ──────────────────────────────────────────────────────────────


def _decode(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_image_thumbnail_fits_the_bounding_box():
    data = thumbnails.generate(FIXTURES / "sample_image.jpg", TYPE_IMAGE, "sample_image.jpg")
    assert data

    image = _decode(data)
    assert image.format == "JPEG"
    assert max(image.size) <= thumbnails.MAX_DIMENSION
    # 800x600 scaled into a 480 box keeps its 4:3 shape.
    assert image.size == (480, 360)


def test_transparent_image_is_composited_not_rejected():
    """JPEG has no alpha channel, so an RGBA source would otherwise raise."""
    data = thumbnails.generate(FIXTURES / "sample_image.png", TYPE_IMAGE, "sample_image.png")
    assert data
    assert _decode(data).mode == "RGB"


def test_unreadable_image_format_is_skipped_not_failed():
    """HEIC and SVG need decoders Pillow does not ship. None is honest; a broken
    thumbnail would be worse."""
    assert thumbnails.generate(FIXTURES / "sample_image.jpg", TYPE_IMAGE, "photo.heic") is None


@needs_ffmpeg
def test_video_thumbnail_is_generated():
    data = thumbnails.generate(
        FIXTURES / "sample_video.mp4", TYPE_VIDEO, "sample_video.mp4", duration_seconds=2.0
    )
    assert data

    image = _decode(data)
    assert image.format == "JPEG"
    assert max(image.size) <= thumbnails.MAX_DIMENSION


@needs_ffmpeg
def test_video_thumbnail_skips_the_black_opening():
    """The fixture opens on half a second of black. A grid of black rectangles
    identifies nothing, which is why the grab seeks in rather than taking frame zero.
    """
    data = thumbnails.generate(
        FIXTURES / "sample_video.mp4", TYPE_VIDEO, "sample_video.mp4", duration_seconds=2.0
    )
    assert data

    image = _decode(data).convert("L")
    # A frame from the colour bars is far from uniformly black.
    assert max(image.getdata()) > 40


@needs_ffmpeg
def test_very_short_video_still_gets_a_thumbnail(tmp_path):
    """A seek computed from duration must not land past the end of a brief clip."""
    import subprocess

    tiny = tmp_path / "tiny.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-nostdin", "-f", "lavfi",
            "-i", "testsrc=size=64x64:rate=15:duration=0.2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tiny),
        ],
        capture_output=True,
        check=True,
    )

    assert thumbnails.generate(tiny, TYPE_VIDEO, "tiny.mp4", duration_seconds=0.2)


def test_pdf_thumbnail_is_generated():
    data = thumbnails.generate(
        FIXTURES / "sample_document.pdf", TYPE_DOCUMENT, "sample_document.pdf"
    )
    assert data

    image = _decode(data)
    assert image.format == "JPEG"
    assert max(image.size) <= thumbnails.MAX_DIMENSION


def test_non_pdf_document_has_no_thumbnail(tmp_path):
    text = tmp_path / "notes.md"
    text.write_text("# heading")
    assert thumbnails.generate(text, TYPE_DOCUMENT, "notes.md") is None


def test_audio_has_no_thumbnail():
    assert thumbnails.generate(FIXTURES / "sample_audio.mp3", TYPE_AUDIO, "sample_audio.mp3") is None


def test_corrupt_file_does_not_raise(tmp_path):
    """A thumbnail is a convenience. Failing to make one must never fail an upload."""
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"\xff\xd8\xff" + b"garbage" * 100)

    assert thumbnails.generate(broken, TYPE_IMAGE, "broken.jpg") is None
