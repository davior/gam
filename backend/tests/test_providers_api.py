"""The AI provider CRUD over HTTP.

Nothing consumes these rows yet — the clients that speak to the endpoints are the next
step of the milestone — so this file is where the configuration is proved to work at all.
The cases that matter most are the ones about the credential: it must be encrypted going
in, unreadable coming out, and removable, which is one more than gecko-notes manages.
"""

import json
import socket

import httpx
import pytest
from sqlmodel import select

from app.auth import decrypt_api_key
from app.models.provider import AIProvider
from app.providers import (
    PROVIDER_ANTHROPIC,
    PROVIDER_CUSTOM,
    PROVIDER_DEEPSEEK,
    PROVIDER_OLLAMA,
    PROVIDER_OPENAI,
)


def _payload(**overrides) -> dict:
    body = {
        "name": "Claude",
        "provider_type": PROVIDER_ANTHROPIC,
        "api_key": "sk-ant-secret",
        "model": "claude-sonnet-4-20250514",
        "supports_images": True,
    }
    body.update(overrides)
    return body


def _create(auth_client, **overrides) -> dict:
    response = auth_client.post("/api/providers", json=_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()["data"]


# ─── the credential ──────────────────────────────────────────────────────────


def test_the_key_is_encrypted_at_rest(auth_client, session):
    created = _create(auth_client)

    row = session.get(AIProvider, created["id"])
    assert "sk-ant-secret" not in row.api_key
    assert row.api_key.startswith("enc:")
    assert decrypt_api_key(row.api_key) == "sk-ant-secret"


def test_the_key_is_never_returned(auth_client):
    created = _create(auth_client)
    assert "sk-ant-secret" not in json.dumps(created)
    assert "api_key" not in created

    listed = auth_client.get("/api/providers").json()
    assert "sk-ant-secret" not in json.dumps(listed)


def test_the_response_says_whether_a_key_is_configured(auth_client):
    """gecko-notes redacts the key to "", which reads identically to having none — so
    its own settings screen cannot tell a configured provider from an unconfigured one."""
    with_key = _create(auth_client)
    assert with_key["api_key_configured"] is True

    without = _create(auth_client, name="Local", provider_type=PROVIDER_OLLAMA, api_key="")
    assert without["api_key_configured"] is False


def test_omitting_the_key_on_update_leaves_it_alone(auth_client, session):
    """The browser never received the key, so it has nothing to send back. An update
    that does not mention it must not wipe it."""
    created = _create(auth_client)

    auth_client.put(f"/api/providers/{created['id']}", json={"name": "Renamed"})

    session.expire_all()
    row = session.get(AIProvider, created["id"])
    assert decrypt_api_key(row.api_key) == "sk-ant-secret"
    assert row.name == "Renamed"


def test_an_empty_key_on_update_removes_it(auth_client, session):
    """gecko-notes writes only `if payload.api_key:`, so a stored key can never be
    cleared there. Same three-state contract as every other credential in this app."""
    created = _create(auth_client)

    body = auth_client.put(f"/api/providers/{created['id']}", json={"api_key": ""})
    assert body.json()["data"]["api_key_configured"] is False

    session.expire_all()
    assert session.get(AIProvider, created["id"]).api_key == ""


def test_a_new_key_replaces_the_old_one(auth_client, session):
    created = _create(auth_client)

    auth_client.put(f"/api/providers/{created['id']}", json={"api_key": "sk-ant-second"})

    session.expire_all()
    assert decrypt_api_key(session.get(AIProvider, created["id"]).api_key) == "sk-ant-second"


# ─── ownership ───────────────────────────────────────────────────────────────


def test_the_list_starts_empty(auth_client):
    body = auth_client.get("/api/providers").json()
    assert body["data"] == []
    assert body["total"] == 0


def test_another_users_provider_is_invisible(auth_client, session):
    """404 rather than 403: whether a provider id exists is not a question this API
    answers for people who do not own it."""
    session.add(
        AIProvider(
            id="someone-elses",
            user_id="a-different-user",
            name="Theirs",
            provider_type=PROVIDER_OPENAI,
            model="gpt-4o",
        )
    )
    session.commit()

    assert auth_client.get("/api/providers").json()["data"] == []
    assert auth_client.put("/api/providers/someone-elses", json={"name": "x"}).status_code == 404
    assert auth_client.delete("/api/providers/someone-elses").status_code == 404
    assert auth_client.post("/api/providers/someone-elses/activate").status_code == 404


def test_authentication_is_required(client):
    assert client.get("/api/providers").status_code == 401
    assert client.post("/api/providers", json=_payload()).status_code == 401


# ─── validation ──────────────────────────────────────────────────────────────


def test_an_unknown_provider_type_is_refused(auth_client):
    response = auth_client.post("/api/providers", json=_payload(provider_type="skynet"))
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "bad_request"


def test_an_unknown_provider_type_is_refused_on_update(auth_client):
    created = _create(auth_client)
    response = auth_client.put(
        f"/api/providers/{created['id']}", json={"provider_type": "skynet"}
    )
    assert response.status_code == 400


def test_a_model_is_free_text(auth_client):
    """No closed dropdown. Models ship faster than this app is redeployed."""
    created = _create(auth_client, model="claude-something-not-released-yet")
    assert created["model"] == "claude-something-not-released-yet"


# ─── base URL and SSRF ───────────────────────────────────────────────────────


def test_a_custom_base_url_must_be_https(auth_client):
    response = auth_client.post(
        "/api/providers",
        json=_payload(provider_type=PROVIDER_CUSTOM, base_url="http://gateway.example.com"),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_url"


def test_a_private_base_url_is_refused(auth_client):
    response = auth_client.post(
        "/api/providers",
        json=_payload(provider_type=PROVIDER_CUSTOM, base_url="https://127.0.0.1:8080"),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "ssrf_blocked"


def test_a_name_resolving_inside_the_network_is_refused(auth_client, monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    response = auth_client.post(
        "/api/providers",
        json=_payload(provider_type=PROVIDER_CUSTOM, base_url="https://gateway.example.com"),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "ssrf_blocked"


def test_changing_only_the_base_url_is_still_checked(auth_client):
    """The bug gecko-notes had and fixed: the check rode inside the `if payload.api_key`
    block, so an update that touched only the URL skipped it — and the stored URL is
    exactly what a later request gets sent to."""
    created = _create(auth_client, provider_type=PROVIDER_CUSTOM, base_url="https://8.8.8.8")

    response = auth_client.put(
        f"/api/providers/{created['id']}", json={"base_url": "https://192.168.0.1"}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "ssrf_blocked"


def test_changing_type_and_url_together_is_checked_against_the_new_type(auth_client):
    """An ollama row moving to `custom` brings its private URL with it unless the check
    uses the type this request results in rather than the one already stored."""
    created = _create(
        auth_client,
        provider_type=PROVIDER_OLLAMA,
        api_key="",
        base_url="http://localhost:11434",
        model="llama3.2",
    )

    response = auth_client.put(
        f"/api/providers/{created['id']}",
        json={"provider_type": PROVIDER_CUSTOM, "base_url": "http://localhost:11434"},
    )
    assert response.status_code == 400


def test_ollama_may_point_at_a_private_address(auth_client):
    """The one deliberate exception. It is a local daemon; that is the point of it."""
    created = _create(
        auth_client,
        name="Local",
        provider_type=PROVIDER_OLLAMA,
        api_key="",
        base_url="http://localhost:11434",
        model="llama3.2",
    )
    assert created["base_url"] == "http://localhost:11434"


def test_a_deepseek_base_url_is_not_checked_because_it_is_never_used(auth_client):
    """DeepSeek resolves to a fixed managed endpoint, so the stored value is inert."""
    created = _create(
        auth_client,
        provider_type=PROVIDER_DEEPSEEK,
        base_url="http://whatever.invalid",
        model="deepseek-chat",
    )
    assert created["id"]


# ─── activation ──────────────────────────────────────────────────────────────


def test_activating_one_provider_deactivates_the_rest(auth_client):
    first = _create(auth_client, name="Claude", is_active=True)
    second = _create(auth_client, name="DeepSeek", provider_type=PROVIDER_DEEPSEEK, model="deepseek-chat")

    auth_client.post(f"/api/providers/{second['id']}/activate")

    listed = {p["id"]: p for p in auth_client.get("/api/providers").json()["data"]}
    assert listed[first["id"]]["is_active"] is False
    assert listed[second["id"]]["is_active"] is True


def test_creating_an_active_provider_deactivates_the_previous_one(auth_client):
    first = _create(auth_client, name="Claude", is_active=True)
    _create(auth_client, name="OpenAI", provider_type=PROVIDER_OPENAI, model="gpt-4o", is_active=True)

    listed = {p["id"]: p for p in auth_client.get("/api/providers").json()["data"]}
    assert listed[first["id"]]["is_active"] is False
    assert sum(p["is_active"] for p in listed.values()) == 1


def test_updating_a_provider_to_active_deactivates_the_others(auth_client):
    first = _create(auth_client, name="Claude", is_active=True)
    second = _create(auth_client, name="DeepSeek", provider_type=PROVIDER_DEEPSEEK, model="deepseek-chat")

    auth_client.put(f"/api/providers/{second['id']}", json={"is_active": True})

    listed = {p["id"]: p for p in auth_client.get("/api/providers").json()["data"]}
    assert listed[first["id"]]["is_active"] is False
    assert listed[second["id"]]["is_active"] is True


# ─── extra params ────────────────────────────────────────────────────────────


def test_extra_params_round_trip_as_a_dict(auth_client, session):
    created = _create(auth_client, extra_params={"temperature": 0, "top_p": 0.9})
    assert created["extra_params"] == {"temperature": 0, "top_p": 0.9}

    # JSON-as-TEXT in the column, per the house convention.
    assert isinstance(session.get(AIProvider, created["id"]).extra_params, str)


def test_empty_extra_params_clears_them(auth_client):
    created = _create(auth_client, extra_params={"temperature": 0})

    body = auth_client.put(f"/api/providers/{created['id']}", json={"extra_params": {}})
    assert body.json()["data"]["extra_params"] is None


def test_unreadable_extra_params_read_as_nothing_rather_than_500(auth_client, session):
    """A row written by an older version should not take the settings screen down."""
    created = _create(auth_client)
    session.get(AIProvider, created["id"]).extra_params = "not json"
    session.commit()

    body = auth_client.get("/api/providers").json()["data"][0]
    assert body["extra_params"] is None


# ─── deletion ────────────────────────────────────────────────────────────────


def test_delete_removes_the_row(auth_client, session):
    created = _create(auth_client)

    assert auth_client.delete(f"/api/providers/{created['id']}").status_code == 204

    session.expire_all()
    assert session.exec(select(AIProvider)).all() == []


# ─── the connection probe ────────────────────────────────────────────────────


@pytest.fixture(name="captured")
def captured_fixture(monkeypatch):
    """Record the outbound probe instead of making it.

    Same shape as tests/test_deepgram.py: patch `httpx.post`, keep everything else real,
    and assert on the URL and headers the app chose.
    """
    calls: list[dict] = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return httpx.Response(200, json={})

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return httpx.Response(200, json={"models": []})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    return calls


def test_an_anthropic_probe_goes_to_anthropic(auth_client, captured):
    body = auth_client.post(
        "/api/providers/test",
        json={
            "provider_type": PROVIDER_ANTHROPIC,
            "api_key": "sk-ant-x",
            "model": "claude-sonnet-4-20250514",
        },
    ).json()["data"]

    assert body["success"] is True
    assert captured[0]["url"] == "https://api.anthropic.com/v1/messages"
    assert captured[0]["headers"]["x-api-key"] == "sk-ant-x"
    assert captured[0]["headers"]["anthropic-version"] == "2023-06-01"


def test_a_deepseek_probe_with_the_flag_goes_to_the_anthropic_compatible_endpoint(
    auth_client, captured
):
    """The whole reason `use_anthropic_api` exists. Testing it against the
    OpenAI-compatible endpoint would report a result for a URL it no longer uses."""
    body = auth_client.post(
        "/api/providers/test",
        json={
            "provider_type": PROVIDER_DEEPSEEK,
            "api_key": "sk-ds-x",
            "model": "deepseek-chat",
            "use_anthropic_api": True,
        },
    ).json()["data"]

    assert body["success"] is True
    assert captured[0]["url"] == "https://api.deepseek.com/anthropic/v1/messages"
    # Both auth headers, because compatible gateways disagree about which to read.
    assert captured[0]["headers"]["x-api-key"] == "sk-ds-x"
    assert captured[0]["headers"]["Authorization"] == "Bearer sk-ds-x"
    # Beta flags are Anthropic-internal; a gateway could reject the whole request over
    # a feature it was never asked for.
    assert "anthropic-beta" not in captured[0]["headers"]


def test_a_deepseek_probe_without_the_flag_goes_to_the_openai_compatible_endpoint(
    auth_client, captured
):
    auth_client.post(
        "/api/providers/test",
        json={"provider_type": PROVIDER_DEEPSEEK, "api_key": "k", "model": "deepseek-chat"},
    )
    assert captured[0]["url"] == "https://api.deepseek.com/v1/chat/completions"


def test_a_deepseek_probe_ignores_a_stored_base_url(auth_client, captured):
    """A fixed managed endpoint, so a crafted base_url cannot redirect the request."""
    auth_client.post(
        "/api/providers/test",
        json={
            "provider_type": PROVIDER_DEEPSEEK,
            "api_key": "k",
            "model": "deepseek-chat",
            "base_url": "https://8.8.8.8",
        },
    )
    assert captured[0]["url"].startswith("https://api.deepseek.com")


def test_an_ollama_probe_asks_for_its_model_list(auth_client, captured):
    body = auth_client.post(
        "/api/providers/test",
        json={
            "provider_type": PROVIDER_OLLAMA,
            "model": "llama3.2",
            "base_url": "http://localhost:11434",
        },
    ).json()["data"]

    assert body["success"] is True
    assert captured[0]["url"] == "http://localhost:11434/api/tags"


def test_a_saved_provider_is_probed_with_its_stored_key(auth_client, captured):
    """The screen can re-test a provider without asking for the key again, which it
    could not do anyway — it was never given it."""
    created = _create(auth_client)

    auth_client.post(
        "/api/providers/test",
        json={
            "provider_id": created["id"],
            "provider_type": PROVIDER_ANTHROPIC,
            "model": "claude-sonnet-4-20250514",
        },
    )
    assert captured[0]["headers"]["x-api-key"] == "sk-ant-secret"


def test_probing_someone_elses_provider_is_a_404(auth_client, session, captured):
    session.add(
        AIProvider(
            id="theirs",
            user_id="a-different-user",
            name="Theirs",
            provider_type=PROVIDER_ANTHROPIC,
            model="claude-sonnet-4-20250514",
        )
    )
    session.commit()

    response = auth_client.post(
        "/api/providers/test",
        json={
            "provider_id": "theirs",
            "provider_type": PROVIDER_ANTHROPIC,
            "model": "claude-sonnet-4-20250514",
        },
    )
    assert response.status_code == 404
    assert captured == []


def test_the_anthropic_probe_is_ssrf_checked(auth_client, captured):
    """gecko-notes checks the OpenAI-compatible branch and not this one, so a `custom`
    provider's payload-supplied base_url goes straight into an outbound POST."""
    response = auth_client.post(
        "/api/providers/test",
        json={
            "provider_type": PROVIDER_CUSTOM,
            "api_key": "k",
            "model": "m",
            "base_url": "https://169.254.169.254",
            "use_anthropic_api": True,
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "ssrf_blocked"
    assert captured == []


@pytest.mark.parametrize(
    "status_code,success",
    [(200, True), (400, True), (401, False), (403, False), (404, False), (429, False), (500, False)],
)
def test_how_a_status_code_is_read(auth_client, monkeypatch, status_code, success):
    """400 counts as success: the endpoint answered, so the address and the credential
    were both accepted — it is the ten-token probe body it did not like, and that is not
    the question being asked."""
    monkeypatch.setattr(
        httpx, "post", lambda url, **kwargs: httpx.Response(status_code, json={})
    )

    body = auth_client.post(
        "/api/providers/test",
        json={"provider_type": PROVIDER_ANTHROPIC, "api_key": "k", "model": "m"},
    ).json()["data"]
    assert body["success"] is success


def test_an_unreachable_provider_reports_a_sentence_not_a_traceback(auth_client, monkeypatch):
    def explode(url, **kwargs):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(httpx, "post", explode)

    body = auth_client.post(
        "/api/providers/test",
        json={"provider_type": PROVIDER_ANTHROPIC, "api_key": "k", "model": "m"},
    ).json()["data"]
    assert body["success"] is False
    assert body["message"]


def test_a_timeout_says_so(auth_client, monkeypatch):
    def slow(url, **kwargs):
        raise httpx.ReadTimeout("too slow")

    monkeypatch.setattr(httpx, "post", slow)

    body = auth_client.post(
        "/api/providers/test",
        json={"provider_type": PROVIDER_ANTHROPIC, "api_key": "k", "model": "m"},
    ).json()["data"]
    assert body["success"] is False
    assert "time" in body["message"].lower()
