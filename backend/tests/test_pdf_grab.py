"""Rendering a PDF page to a picture: the endpoint, what the picture is, and its limits."""

from pathlib import Path

import pytest

from app.models.asset import Asset
from tests.test_extract_text import upload_document

FIXTURES = Path(__file__).parent / "fixtures"


def test_grabbing_a_page_makes_an_image_child(library, session):
    pdf = upload_document(library, ".pdf")

    response = library.post(f"/api/assets/{pdf['id']}/grab-page", json={"page": 1})

    assert response.status_code == 201, response.text
    grabbed = response.json()["data"]
    assert grabbed["asset_type"] == "image"
    assert grabbed["source"] == "pdf_page"
    assert grabbed["parent_asset_id"] == pdf["id"]
    assert grabbed["source_page"] == 1
    assert grabbed["width"] and grabbed["height"]
    assert grabbed["thumb_url"]
    assert grabbed["file_format"] == "png"


def test_the_page_picture_is_not_listed_as_a_clip(library):
    pdf = upload_document(library, ".pdf")
    library.post(f"/api/assets/{pdf['id']}/grab-page", json={"page": 1})

    clips = library.get(f"/api/assets/{pdf['id']}/clips").json()
    assert clips["data"] == []


def test_a_page_picture_survives_deleting_its_pdf(library, session):
    pdf = upload_document(library, ".pdf")
    grabbed = library.post(f"/api/assets/{pdf['id']}/grab-page", json={"page": 1}).json()["data"]

    assert library.delete(f"/api/assets/{pdf['id']}").status_code in (200, 204)

    kept = library.get(f"/api/assets/{grabbed['id']}")
    assert kept.status_code == 200
    assert kept.json()["data"]["parent_asset_id"] is None


def test_a_page_picture_can_go_in_a_playlist(library):
    pdf = upload_document(library, ".pdf")
    grabbed = library.post(f"/api/assets/{pdf['id']}/grab-page", json={"page": 1}).json()["data"]

    made = library.post(
        "/api/playlists",
        json={"name": "Slides", "items": [{"asset_id": grabbed["id"], "image_seconds": 4}]},
    )

    assert made.status_code in (200, 201), made.text
    item = made.json()["data"]["items"][0]
    assert item["asset_id"] == grabbed["id"]


def test_a_page_past_the_end_says_how_many_there_are(library):
    pdf = upload_document(library, ".pdf")
    count = library.get(f"/api/assets/{pdf['id']}/pages").json()["data"]["page_count"]

    response = library.post(f"/api/assets/{pdf['id']}/grab-page", json={"page": count + 1})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "cannot_grab_page"
    assert f"no page {count + 1}" in detail["message"]


def test_page_zero_is_refused_before_it_reaches_the_renderer(library):
    pdf = upload_document(library, ".pdf")
    assert library.post(f"/api/assets/{pdf['id']}/grab-page", json={"page": 0}).status_code == 422


def test_only_a_pdf_can_be_grabbed(library):
    docx = upload_document(library, ".docx")

    response = library.post(f"/api/assets/{docx['id']}/grab-page", json={"page": 1})

    assert response.status_code == 400
    assert "Only a PDF" in response.json()["detail"]["message"]
    assert library.get(f"/api/assets/{docx['id']}/pages").status_code == 400


def test_another_users_pdf_is_not_found(library, session):
    pdf = upload_document(library, ".pdf")
    row = session.get(Asset, pdf["id"])
    row.user_id = "someone-else"
    session.add(row)
    session.commit()

    assert library.post(f"/api/assets/{pdf['id']}/grab-page", json={"page": 1}).status_code == 404
    assert library.get(f"/api/assets/{pdf['id']}/pages").status_code == 404
