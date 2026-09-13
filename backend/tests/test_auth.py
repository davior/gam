"""Verification of a gecko-notes session.

These build real tokens rather than overriding the dependency: the whole premise of the
suite is that a token Notes signed is accepted here, and an override would prove
nothing about that.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.auth import UserCtx, current_user
from app.config import settings
from app.database import get_session
from app.main import app as fastapi_app


def make_token(
    *, sub="notes-user-1", username="davior", expires_in_minutes=60, secret=None
) -> str:
    """Mint a token shaped exactly like the one gecko-notes issues.

    Its claims are thin — sub, username, exp — with no iss, aud or is_admin. Matching
    that shape here is the point: a test that invented richer claims would pass while
    the real integration failed.
    """
    payload = {
        "sub": sub,
        "username": username,
        "exp": datetime.utcnow() + timedelta(minutes=expires_in_minutes),
    }
    return jwt.encode(
        payload, secret or settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )


def test_bearer_header_is_accepted(client):
    response = client.get("/api/me", headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 200
    assert response.json()["data"] == {"id": "notes-user-1", "username": "davior"}


def test_session_cookie_is_accepted(client):
    """What makes a browser already signed in to Notes arrive here signed in."""
    client.cookies.set(settings.auth_cookie_name, make_token())
    response = client.get("/api/me")
    assert response.status_code == 200
    assert response.json()["data"]["id"] == "notes-user-1"


def test_header_wins_over_cookie(client):
    """An explicit token must not be overridden by whatever session the browser holds."""
    client.cookies.set(settings.auth_cookie_name, make_token(sub="cookie-user"))
    response = client.get(
        "/api/me",
        headers={"Authorization": f"Bearer {make_token(sub='header-user')}"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["id"] == "header-user"


def test_no_credentials_is_rejected(client):
    assert client.get("/api/me").status_code == 401


def test_expired_token_is_rejected(client):
    token = make_token(expires_in_minutes=-1)
    response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_token_signed_with_another_secret_is_rejected(client):
    """The security property the whole shared-secret design rests on."""
    token = make_token(secret="a-different-secret-entirely")
    response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_token_without_subject_is_rejected(client):
    token = jwt.encode(
        {"username": "davior", "exp": datetime.utcnow() + timedelta(minutes=60)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_malformed_header_is_rejected(client):
    """A token with no "Bearer " prefix is not a credential."""
    response = client.get("/api/me", headers={"Authorization": make_token()})
    assert response.status_code == 401


def test_dev_auth_requires_development_environment(client, monkeypatch):
    """DEV_AUTH_USER alone must not disable authentication.

    A production .env that inherited the variable from a dev template would otherwise
    let every anonymous request through as that user.
    """
    monkeypatch.setattr(settings, "dev_auth_user", "local-dev")
    monkeypatch.setattr(settings, "environment", "production")
    assert client.get("/api/me").status_code == 401

    monkeypatch.setattr(settings, "environment", "development")
    response = client.get("/api/me")
    assert response.status_code == 200
    assert response.json()["data"]["id"] == "local-dev"


def test_dev_auth_does_not_shadow_a_real_token(client, monkeypatch):
    """With dev auth on, a token that is present is still the one that decides."""
    monkeypatch.setattr(settings, "dev_auth_user", "local-dev")
    monkeypatch.setattr(settings, "environment", "development")
    response = client.get(
        "/api/me", headers={"Authorization": f"Bearer {make_token(sub='real-user')}"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["id"] == "real-user"


def test_dependency_override_still_works(auth_client):
    """Guards the auth_client fixture the rest of the suite relies on."""
    response = auth_client.get("/api/me")
    assert response.status_code == 200
    assert response.json()["data"]["username"] == "tester"


# ─── CSRF on the cookie path ─────────────────────────────────────────────────
#
# The cookie is an ambient credential: the browser attaches it to cross-site requests
# on its own. A bearer token cannot be attached that way, which is why only this path
# needs a guard.


def test_cross_origin_cookie_write_is_refused(real_auth_library):
    """The hole this closes.

    A cross-origin POST carrying multipart/form-data is a *simple* request, so no
    preflight stands between an attacker's page and this endpoint. CORS would stop them
    reading the response; it would not stop the upload landing in someone's library.
    """
    real_auth_library.cookies.set("gecko_session", make_token())

    response = real_auth_library.post(
        "/api/assets",
        files=[("files", ("evil.jpg", b"\xff\xd8\xff", "image/jpeg"))],
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "forbidden_origin"


def test_cross_origin_cookie_write_is_refused_via_referer(real_auth_library):
    """A request with no Origin but a Referer is judged on the Referer."""
    real_auth_library.cookies.set("gecko_session", make_token())

    response = real_auth_library.post(
        "/api/assets",
        files=[("files", ("evil.jpg", b"\xff\xd8\xff", "image/jpeg"))],
        headers={"Referer": "https://evil.example/attack.html"},
    )

    assert response.status_code == 403


def test_cookie_write_with_no_origin_at_all_is_refused(client):
    """Fails closed. A browser always sends one on a state-changing request, so its
    absence means this did not come from a browser doing what a browser does."""
    client.cookies.set("gecko_session", make_token())

    response = client.request("DELETE", "/api/assets/whatever")
    assert response.status_code == 403


def test_same_origin_cookie_write_is_allowed(real_auth_library):
    """GAM's own frontend must keep working — it is served from the same origin as the
    API, and it authenticates by cookie once the suite session exists."""
    real_auth_library.cookies.set("gecko_session", make_token())

    response = real_auth_library.post(
        "/api/assets",
        files=[("files", ("photo.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 64, "image/jpeg"))],
        # TestClient's base_url is http://testserver, so this is same-origin.
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code in (201, 400), response.text
    assert response.status_code != 403


def test_an_allowlisted_origin_cookie_write_is_allowed(real_auth_library, monkeypatch):
    """A configured sibling origin passes, which is what CORS_ORIGIN is for."""
    from app.config import settings

    monkeypatch.setattr(settings, "cors_origin", "https://notes.geckopico.com")
    real_auth_library.cookies.set("gecko_session", make_token())

    response = real_auth_library.post(
        "/api/assets",
        files=[("files", ("photo.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 64, "image/jpeg"))],
        headers={"Origin": "https://notes.geckopico.com"},
    )

    assert response.status_code != 403


def test_cookie_reads_are_unaffected(real_auth_library):
    """Safe methods change nothing, so a foreign Origin on a GET is not a threat — and
    refusing them would break an <img> or <video> pointed at a signed URL."""
    real_auth_library.cookies.set("gecko_session", make_token())

    response = real_auth_library.get("/api/assets", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200


def test_the_header_path_is_exempt(real_auth_library):
    """A bearer token cannot be attached by a cross-site form or image, so the guard
    does not apply — and applying it would break every API client that is not a
    browser."""
    response = real_auth_library.post(
        "/api/assets",
        files=[("files", ("photo.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 64, "image/jpeg"))],
        headers={
            "Authorization": f"Bearer {make_token()}",
            "Origin": "https://evil.example",
        },
    )

    assert response.status_code != 403


def test_a_lookalike_origin_does_not_pass(real_auth_library, monkeypatch):
    """Host comparison, not a prefix match: notes.geckopico.com.evil.example must not
    satisfy an allowlist entry of notes.geckopico.com."""
    from app.config import settings

    monkeypatch.setattr(settings, "cors_origin", "https://notes.geckopico.com")
    real_auth_library.cookies.set("gecko_session", make_token())

    response = real_auth_library.post(
        "/api/assets",
        files=[("files", ("evil.jpg", b"\xff\xd8\xff", "image/jpeg"))],
        headers={"Origin": "https://notes.geckopico.com.evil.example"},
    )

    assert response.status_code == 403
