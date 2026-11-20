"""`POST /api/assets/import-image`: one picture an image search found, filed in the library.

The network is replaced at `ingest/web.py`'s client, so the real download code runs
(type sniffing, the cap, the redirect check) and the bytes go through the upload's own
ingest. DNS is faked to a public address, so the SSRF check runs without touching it.
"""

import socket
from pathlib import Path

import httpx
import pytest
from sqlmodel import select

from app.ingest import web
from app.models.asset import PROVENANCE_EMBEDDED, Asset

FIXTURES = Path(__file__).parent / "fixtures"
JPEG = (FIXTURES / "sample_image.jpg").read_bytes()
PNG = (FIXTURES / "sample_image.png").read_bytes()


def _resolving_to(address):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    return fake_getaddrinfo


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to("93.184.216.34"))


@pytest.fixture
def serve(monkeypatch):
    """Answer `web`'s requests from `serve.routes` (url -> Response). `serve` is also the
    list of URLs asked for."""

    class Asked(list):
        routes: dict[str, httpx.Response]

    asked = Asked()
    asked.routes = {}
    routes = asked.routes

    def handler(request):
        asked.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))

    def client(accept_encoding):
        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False,
                            headers={"Accept-Encoding": accept_encoding})

    monkeypatch.setattr(web, "_client", client)
    return asked


def _jpeg():
    return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=JPEG)


def test_a_picture_is_downloaded_catalogued_and_credited_to_its_page(library, session, serve):
    serve.routes["https://cdn.example.org/owl.jpg"] = _jpeg()
    response = library.post("/api/assets/import-image", json={
        "image_url": "https://cdn.example.org/owl.jpg",
        "page_url": "https://birds.example.org/owls",
        "title": "Barn owl at dusk",
        "description": "A barn owl  in flight over a field",
        "tags": ["owls", "web"],
    })
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert (data["name"], data["asset_type"]) == ("Barn owl at dusk", "image")
    assert data["thumb_url"] and data["file_url"]

    asset = session.get(Asset, data["id"])
    assert asset.source_url == "https://birds.example.org/owls"
    assert asset.retrieved_at is not None
    assert asset.description == "A barn owl in flight over a field"
    # What the search said, unchecked; and nothing is claimed about the rights.
    assert PROVENANCE_EMBEDDED in (asset.field_provenance or "")
    assert asset.license is None and asset.creator is None
    assert sorted(t["name"] for t in data["tags"]) == ["owls", "web"]


def test_the_same_picture_twice_is_one_asset(library, session, serve):
    serve.routes["https://cdn.example.org/owl.jpg"] = _jpeg()
    serve.routes["https://cdn.example.org/owl-large.jpg"] = _jpeg()
    first = library.post("/api/assets/import-image", json={"image_url": "https://cdn.example.org/owl.jpg"})
    assert first.status_code == 201
    again = library.post("/api/assets/import-image", json={"image_url": "https://cdn.example.org/owl.jpg"})
    # Known by its address, so not even downloaded again.
    assert again.status_code == 200 and again.json()["data"]["id"] == first.json()["data"]["id"]
    assert serve.count("https://cdn.example.org/owl.jpg") == 1
    # Another address with the same bytes is known by its checksum.
    mirror = library.post("/api/assets/import-image", json={"image_url": "https://cdn.example.org/owl-large.jpg",
                                                            "page_url": "https://elsewhere.example/p"})
    assert mirror.status_code == 200 and mirror.json()["data"]["id"] == first.json()["data"]["id"]
    assert len(session.exec(select(Asset)).all()) == 1


def test_the_servers_type_names_the_file_not_the_path(library, session, serve):
    # A CDN sending PNG (or WebP) from a `.jpg` path: stored as what it is.
    serve.routes["https://cdn.example.org/x.jpg"] = httpx.Response(
        200, headers={"content-type": "image/png"}, content=PNG)
    data = library.post("/api/assets/import-image", json={"image_url": "https://cdn.example.org/x.jpg"}).json()["data"]
    assert session.get(Asset, data["id"]).storage_key.endswith(".png")


def test_a_page_instead_of_a_picture_is_refused(library, session, serve):
    serve.routes["https://site.example.org/photo?id=4"] = httpx.Response(
        200, headers={"content-type": "text/html"}, content=b"<html>hotlink denied</html>")
    response = library.post("/api/assets/import-image", json={"image_url": "https://site.example.org/photo?id=4"})
    assert response.status_code == 400
    assert response.json()["detail"] == {"code": "image_unavailable",
                                         "message": "That link is not a picture GAM can store"}
    assert session.exec(select(Asset)).all() == []


def test_svg_is_not_taken_from_the_web(library, serve):
    serve.routes["https://cdn.example.org/logo.svg"] = httpx.Response(
        200, headers={"content-type": "image/svg+xml"}, content=b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    assert library.post("/api/assets/import-image",
                        json={"image_url": "https://cdn.example.org/logo.svg"}).status_code == 400


def test_an_inward_address_is_refused_before_any_fetch(library, serve, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to("10.0.0.5"))
    response = library.post("/api/assets/import-image", json={"image_url": "https://intranet.example/x.jpg"})
    assert response.status_code == 400 and response.json()["detail"]["code"] == "unsafe_url"
    assert serve == []


def test_a_redirect_inward_is_refused(library, serve):
    serve.routes["https://cdn.example.org/r.jpg"] = httpx.Response(302, headers={"location": "https://127.0.0.1/x.jpg"})
    response = library.post("/api/assets/import-image", json={"image_url": "https://cdn.example.org/r.jpg"})
    assert response.status_code == 400
    assert "not one this app will fetch" in response.json()["detail"]["message"]


def test_a_picture_over_the_cap_is_refused(serve, tmp_path):
    serve.routes["https://cdn.example.org/huge.jpg"] = _jpeg()
    with pytest.raises(web.WebFetchError, match="picture is larger than"):
        web.download_image("https://cdn.example.org/huge.jpg", tmp_path, "p", max_bytes=10)
    assert list(tmp_path.iterdir()) == []


def test_a_page_address_must_be_a_web_address(library, serve):
    serve.routes["https://cdn.example.org/owl.jpg"] = _jpeg()
    response = library.post("/api/assets/import-image", json={"image_url": "https://cdn.example.org/owl.jpg",
                                                              "page_url": "javascript:alert(1)"})
    assert response.status_code == 400


def test_signed_out_is_refused(client):
    assert client.post("/api/assets/import-image", json={"image_url": "https://x.example/a.jpg"}).status_code == 401
