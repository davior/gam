"""The settings the browser is allowed to know.

`/api/config` exists because a Vite `import.meta.env` value is fixed at build time, so
`NOTES_BASE_URL` could be set in `.env`, plumbed through docker-compose, and still have
no effect on where the login button pointed.
"""

from app.config import settings


def test_it_serves_the_configured_notes_url(client, monkeypatch):
    monkeypatch.setattr(settings, "notes_base_url", "https://notes.example.test")

    body = client.get("/api/config").json()

    assert body["data"]["notes_base_url"] == "https://notes.example.test"


def test_it_needs_no_session(client):
    """Load-bearing, not a convenience.

    This endpoint carries the address of the sign-in page. Requiring a session to read
    it would mean you had to be signed in to find out where to sign in — a login flow
    that deadlocks for exactly the people who need it.

    `client` is the unauthenticated fixture: it sends no token and no cookie.
    """
    assert client.get("/api/config").status_code == 200


def test_it_exposes_nothing_but_what_it_promises(client):
    """The payload is public, so its field set is pinned.

    Anyone who can reach the app can read this. `Settings` also holds the JWT secret
    that signs every session and derives the key encrypting stored provider API keys —
    so an endpoint that grew a field by accident would be a real leak, not an untidy
    response. Adding one here should require changing this test on purpose.
    """
    data = client.get("/api/config").json()["data"]

    assert set(data) == {"notes_base_url"}
    assert settings.jwt_secret_key not in str(data)
