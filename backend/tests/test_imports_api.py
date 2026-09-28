"""`POST /api/assets/import` — queueing an import, and what it refuses up front."""

import json
import socket

import pytest
from sqlmodel import select

from app.models.job import KIND_IMPORT_URL, EnrichmentJob


def _resolving_to(address):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    return fake_getaddrinfo


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Every name resolves somewhere public, so these tests never touch real DNS."""
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to("93.184.216.34"))


def _job(session, job_id):
    return session.get(EnrichmentJob, job_id)


def test_an_import_is_queued_as_a_job(auth_client, session):
    response = auth_client.post(
        "/api/assets/import", json={"url": "https://www.youtube.com/watch?v=abc123"}
    )

    assert response.status_code == 202
    body = response.json()["data"]
    assert body["action"] == KIND_IMPORT_URL
    assert body["status"] == "queued"
    assert body["asset_id"] is None
    # Not "" — that renders as "Your whole library" in the activity feed.
    assert body["asset_name"] == "https://www.youtube.com/watch?v=abc123"

    payload = json.loads(_job(session, body["id"]).payload)
    assert payload == {
        "url": "https://www.youtube.com/watch?v=abc123",
        "audio_only": False,
        "apply_tags": True,
        "chapters_as_clips": True,
        "depth": 0,
    }


def test_options_are_carried_to_the_job(auth_client, session):
    response = auth_client.post(
        "/api/assets/import",
        json={
            "url": "https://youtu.be/abc123",
            "audio_only": True,
            "apply_tags": False,
            "chapters_as_clips": False,
        },
    )
    payload = json.loads(_job(session, response.json()["data"]["id"]).payload)
    assert payload["audio_only"] is True
    assert payload["apply_tags"] is False
    assert payload["chapters_as_clips"] is False


@pytest.mark.parametrize(
    ("pasted", "stored"),
    [
        ("youtu.be/abc123", "https://youtu.be/abc123"),
        ("  https://youtu.be/abc123  ", "https://youtu.be/abc123"),
        ("http://www.youtube.com/watch?v=abc123", "https://www.youtube.com/watch?v=abc123"),
        ("HTTP://rumble.com/v1-x.html", "https://rumble.com/v1-x.html"),
    ],
)
def test_links_are_normalised_the_way_people_paste_them(auth_client, session, pasted, stored):
    response = auth_client.post("/api/assets/import", json={"url": pasted})
    assert response.status_code == 202
    assert json.loads(_job(session, response.json()["data"]["id"]).payload)["url"] == stored


@pytest.mark.parametrize(
    "url",
    ["ftp://example.com/video.mp4", "file:///etc/passwd", "javascript:alert(1)", "mailto:a@b.com"],
)
def test_non_web_links_are_refused(auth_client, url):
    response = auth_client.post("/api/assets/import", json={"url": url})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_url"


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/video",
        "https://localhost/video",
        "https://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5:8080/admin",
    ],
)
def test_internal_addresses_are_refused(auth_client, url):
    response = auth_client.post("/api/assets/import", json={"url": url})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "ssrf_blocked"


def test_a_name_that_resolves_inward_is_refused(auth_client, monkeypatch):
    """The case a literal-IP check alone misses: a name the attacker controls."""
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to("10.1.2.3"))
    response = auth_client.post(
        "/api/assets/import", json={"url": "https://innocent-looking.example/video"}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "ssrf_blocked"


def test_a_refused_link_queues_nothing(auth_client, session):
    auth_client.post("/api/assets/import", json={"url": "https://127.0.0.1/video"})
    assert session.exec(select(EnrichmentJob)).all() == []


@pytest.mark.parametrize("body", [{}, {"url": ""}, {"url": "https://x.com/" + "a" * 2100}])
def test_malformed_requests_are_422(auth_client, body):
    assert auth_client.post("/api/assets/import", json=body).status_code == 422


def test_whitespace_only_is_refused(auth_client):
    response = auth_client.post("/api/assets/import", json={"url": "   "})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_url"


def test_importing_requires_authentication(client):
    response = client.post("/api/assets/import", json={"url": "https://youtu.be/abc123"})
    assert response.status_code == 401
