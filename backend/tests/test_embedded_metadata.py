"""Attribution read out of a file's own container metadata (M10).

Run against real files rather than mocked tag dictionaries: the thing most likely to be
wrong here is what a format actually stores and where, which a stub cannot be wrong
about. `tests/make_fixtures.py` builds the `attributed_*` set; the `sample_*` set
deliberately carries no attribution, which is what proves the harvester stays silent
when there is nothing to read.
"""

from datetime import datetime
from pathlib import Path

import pytest
from sqlmodel import select

from app.ingest.embedded_metadata import harvest, normalise_partial_date
from app.ingest.probe import probe
from app.models.asset import PROVENANCE_EMBEDDED, Asset

FIXTURES = Path(__file__).parent / "fixtures"


def _harvest(name: str, asset_type: str) -> dict:
    path = FIXTURES / name
    tags = probe(path).tags if asset_type in ("video", "audio") else {}
    return harvest(path, asset_type, name, probe_tags=tags)


def _upload(client, *names: str):
    files = [
        ("files", (name, (FIXTURES / name).read_bytes(), "application/octet-stream"))
        for name in names
    ]
    return client.post("/api/assets", files=files)


# ─── per format ──────────────────────────────────────────────────────────────


def test_mp4_tag_block_is_read():
    found = _harvest("attributed_video.mp4", "video")
    assert found["creator"] == "Jane Doe"
    assert found["source_title"] == "Panorama"
    assert found["license"] == "(C) 2019 BBC"
    assert found["published_date"] == "2019-03-15"


def test_id3_tags_are_read():
    found = _harvest("attributed_audio.mp3", "audio")
    assert found["creator"] == "Jane Doe"
    assert found["publisher"] == "BBC"
    assert found["source_title"] == "Panorama"


def test_a_bare_year_stays_a_bare_year():
    """The case the string column exists for: ID3 routinely carries only a year, and
    turning it into 2019-01-01 would invent a precision the file never claimed."""
    assert _harvest("attributed_audio.mp3", "audio")["published_date"] == "2019"


def test_exif_is_read_including_the_sub_ifd():
    """DateTimeOriginal lives in the Exif sub-IFD (0x8769), where every real camera puts
    it — a harvester reading only IFD0 passes on hand-built files and fails on photos."""
    found = _harvest("attributed_image.jpg", "image")
    assert found["creator"] == "Jane Doe"
    assert found["license"] == "(C) 2019 BBC"
    assert found["published_date"] == "2019-03-15"


def test_pdf_info_dictionary_is_read():
    found = _harvest("attributed_document.pdf", "document")
    assert found["creator"] == "Jane Doe"
    assert found["source_title"] == "Panorama"
    # D:20190315101112Z — prefixed and separator-less, unlike every other format.
    assert found["published_date"] == "2019-03-15"


def test_docx_core_properties_are_read():
    found = _harvest("attributed_document.docx", "document")
    assert found["creator"] == "Jane Doe"
    assert found["source_title"] == "Panorama"
    assert found["published_date"] == "2019-03-15"


# ─── what must NOT be harvested ──────────────────────────────────────────────


def test_a_media_containers_title_is_not_harvested():
    """In a media container `title` names this file, not a containing work, and is very
    often an encoder's boilerplate. The fixture sets one precisely so this can assert it
    goes nowhere — mapping it would rename or mis-source half a library on upload."""
    found = _harvest("attributed_video.mp4", "video")
    assert "Encoder boilerplate" not in str(found)
    assert found.get("source_title") == "Panorama"
    assert "name" not in found


def test_technical_tags_are_not_mistaken_for_attribution():
    """Every MP4 carries encoder, handler_name, major_brand and compatible_brands. A
    mapping that took whatever it recognised would file "Lavf60.16.100" as a creator."""
    found = _harvest("sample_video.mp4", "video")
    assert found == {}


def test_files_with_no_attribution_yield_nothing():
    assert _harvest("sample_image.jpg", "image") == {}
    assert _harvest("sample_document.pdf", "document") == {}
    assert _harvest("sample_audio.mp3", "audio") == {}


def test_retrieved_at_is_never_harvested():
    """When *you* fetched something is not a fact the file can know."""
    for name, kind in [
        ("attributed_video.mp4", "video"),
        ("attributed_image.jpg", "image"),
        ("attributed_document.pdf", "document"),
    ]:
        assert "retrieved_at" not in _harvest(name, kind)


def test_a_comment_is_only_taken_as_a_url_when_it_is_one():
    """Downloaders put the source URL in `comment`. They also put everything else there."""
    from app.ingest.embedded_metadata import _from_container_tags

    assert _from_container_tags({"comment": "https://example.org/x"})["source_url"] == (
        "https://example.org/x"
    )
    assert "source_url" not in _from_container_tags({"comment": "Recorded off-air, poor audio"})


def test_harvest_never_raises_on_an_unreadable_file(tmp_path):
    """This runs after the row is committed; an exception here must not reach the upload."""
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    assert harvest(broken, "image", "broken.jpg") == {}


def test_an_unknown_document_format_is_simply_empty():
    assert _harvest("sample_document.txt", "document") == {}


# ─── the date normaliser ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2019", "2019"),
        ("2019-03", "2019-03"),
        ("2019-03-15", "2019-03-15"),
        ("2019-03-15T10:00:00.000000Z", "2019-03-15"),  # MP4
        ("2019:03:15 10:11:12", "2019-03-15"),          # EXIF, colon separated
        ("D:20190315101112Z", "2019-03-15"),            # PDF
        ("20190315", "2019-03-15"),                     # compact, no prefix
        ("", None),
        ("not a date", None),
        (None, None),
        ("2019-19-40", "2019"),   # degrades to what is still trustworthy
    ],
)
def test_partial_dates_normalise_without_inventing_precision(raw, expected):
    assert normalise_partial_date(raw) == expected


def test_a_real_datetime_is_accepted():
    """Office hands back a datetime object rather than a string."""
    assert normalise_partial_date(datetime(2020, 5, 6, 12, 0)) == "2020-05-06"


# ─── through the upload pipeline ─────────────────────────────────────────────


def test_an_upload_lands_attributed(library, session):
    response = _upload(library, "attributed_image.jpg")
    assert response.status_code == 201

    asset = response.json()["created"][0]
    assert asset["creator"] == "Jane Doe"
    assert asset["license"] == "(C) 2019 BBC"
    assert asset["published_date"] == "2019-03-15"


def test_embedded_values_are_stamped_as_embedded_not_human(library, session):
    """The distinction that lets a later pass propose over a camera-supplied name
    without ever proposing over something the user typed."""
    _upload(library, "attributed_image.jpg")

    stored = session.exec(select(Asset)).first()
    import json

    provenance = json.loads(stored.field_provenance)
    assert provenance["creator"] == PROVENANCE_EMBEDDED


def test_an_upload_with_no_embedded_metadata_stays_unattributed(library, session):
    response = _upload(library, "sample_image.jpg")
    asset = response.json()["created"][0]

    assert asset["creator"] is None
    assert asset["credit"] == ""


def test_a_harvest_never_overwrites_a_value_already_there(library, session):
    """Filtering to empty fields is what makes the harvest safe to run unasked, and safe
    to re-run over a library that has been hand-corrected."""
    from app.services import assets as asset_service

    _upload(library, "attributed_image.jpg")
    stored = session.exec(select(Asset)).first()

    asset_service.apply_metadata(session, stored, {"creator": "The actual photographer"})
    asset_service.apply_embedded_attribution(
        session, stored, {"creator": "Jane Doe", "publisher": "BBC"}
    )
    session.refresh(stored)

    assert stored.creator == "The actual photographer"
    # The empty one is still filled, so a partial correction does not block the rest.
    assert stored.publisher == "BBC"
