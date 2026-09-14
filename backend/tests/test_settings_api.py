"""The settings API over HTTP.

The router had no HTTP coverage at all before this file, which is part of how the
embedding half of it came to be missing: `settings_store` defined the keys,
`build_embedder` read them, and nothing in between could write them.
"""

import json

from sqlmodel import select

from app.embeddings import PROVIDER_OLLAMA, PROVIDER_OPENAI
from app.models.setting import UserSetting
from app.settings_store import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    EMBEDDING_PROVIDER,
    OPENAI_API_KEY,
    get_setting,
    set_setting,
)

TEST_USER = "user-under-test"


# ─── the gap this file exists to close ───────────────────────────────────────


def test_an_embedding_provider_can_actually_be_configured(auth_client, session):
    """The whole point. Until this endpoint existed `build_embedder` returned None for
    every user forever, so semantic search could not be switched on by anybody."""
    from app.embeddings import build_embedder

    assert build_embedder(session, TEST_USER) is None

    response = auth_client.put(
        "/api/settings/embeddings",
        json={"provider": PROVIDER_OPENAI, "openai_api_key": "sk-test-key"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["configured"] is True

    assert build_embedder(session, TEST_USER) is not None


# ─── reading ─────────────────────────────────────────────────────────────────


def test_defaults_before_anything_is_set(auth_client):
    body = auth_client.get("/api/settings/embeddings").json()["data"]

    assert body["provider"] == PROVIDER_OPENAI
    assert body["model"] == "text-embedding-3-small"
    assert body["dimensions"] == 512
    assert body["openai_key_configured"] is False
    # No key yet, so OpenAI cannot run — which is what the search view reports.
    assert body["configured"] is False
    assert set(body["available_providers"]) == {PROVIDER_OPENAI, PROVIDER_OLLAMA}


def test_the_key_is_never_returned(auth_client, session):
    auth_client.put("/api/settings/embeddings", json={"openai_api_key": "sk-secret"})

    body = auth_client.get("/api/settings/embeddings").json()["data"]
    assert body["openai_key_configured"] is True
    assert "sk-secret" not in json.dumps(body)


def test_the_key_is_encrypted_at_rest(auth_client, session):
    auth_client.put("/api/settings/embeddings", json={"openai_api_key": "sk-secret"})

    row = session.exec(
        select(UserSetting).where(
            UserSetting.user_id == TEST_USER, UserSetting.key == OPENAI_API_KEY
        )
    ).first()
    assert "sk-secret" not in row.value


def test_ollama_needs_no_key_to_be_configured(auth_client):
    body = auth_client.put(
        "/api/settings/embeddings", json={"provider": PROVIDER_OLLAMA}
    ).json()["data"]

    assert body["provider"] == PROVIDER_OLLAMA
    assert body["openai_key_configured"] is False
    # Local inference: nothing to authenticate against, so it is ready as it stands.
    assert body["configured"] is True


# ─── writing ─────────────────────────────────────────────────────────────────


def test_switching_provider_resets_the_model(auth_client, session):
    """Otherwise the OpenAI model name follows the user to Ollama, and the first embed
    job asks a local server for "text-embedding-3-small"."""
    auth_client.put(
        "/api/settings/embeddings",
        json={"provider": PROVIDER_OPENAI, "model": "text-embedding-3-large"},
    )
    assert get_setting(session, TEST_USER, EMBEDDING_MODEL) == "text-embedding-3-large"

    body = auth_client.put(
        "/api/settings/embeddings", json={"provider": PROVIDER_OLLAMA}
    ).json()["data"]

    assert body["model"] == "nomic-embed-text"
    assert get_setting(session, TEST_USER, EMBEDDING_MODEL) == "nomic-embed-text"


def test_re_selecting_the_same_provider_keeps_the_chosen_model(auth_client, session):
    """A no-op provider change must not be a reset, or saving any other field on the
    settings screen silently reverts the user's model choice."""
    auth_client.put(
        "/api/settings/embeddings",
        json={"provider": PROVIDER_OPENAI, "model": "text-embedding-3-large"},
    )
    auth_client.put("/api/settings/embeddings", json={"provider": PROVIDER_OPENAI})

    assert get_setting(session, TEST_USER, EMBEDDING_MODEL) == "text-embedding-3-large"


def test_a_model_named_in_the_same_request_survives_the_switch(auth_client, session):
    auth_client.put(
        "/api/settings/embeddings",
        json={"provider": PROVIDER_OLLAMA, "model": "mxbai-embed-large"},
    )
    assert get_setting(session, TEST_USER, EMBEDDING_MODEL) == "mxbai-embed-large"


def test_an_empty_key_clears_it(auth_client, session):
    """`None` means leave alone and `""` means remove — without the distinction a stored
    key could never be cleared."""
    auth_client.put("/api/settings/embeddings", json={"openai_api_key": "sk-test"})
    assert auth_client.get("/api/settings/embeddings").json()["data"]["openai_key_configured"]

    auth_client.put("/api/settings/embeddings", json={"openai_api_key": ""})
    assert not auth_client.get("/api/settings/embeddings").json()["data"]["openai_key_configured"]


def test_omitting_the_key_leaves_it_alone(auth_client):
    auth_client.put("/api/settings/embeddings", json={"openai_api_key": "sk-test"})
    auth_client.put("/api/settings/embeddings", json={"model": "text-embedding-3-large"})

    body = auth_client.get("/api/settings/embeddings").json()["data"]
    assert body["openai_key_configured"] is True
    assert body["model"] == "text-embedding-3-large"


def test_dimensions_round_trip(auth_client, session):
    auth_client.put("/api/settings/embeddings", json={"dimensions": 1024})
    assert int(get_setting(session, TEST_USER, EMBEDDING_DIMENSIONS)) == 1024


def test_an_unknown_provider_is_rejected(auth_client, session):
    response = auth_client.put("/api/settings/embeddings", json={"provider": "pinecone"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "bad_request"
    assert get_setting(session, TEST_USER, EMBEDDING_PROVIDER) is None


def test_zero_dimensions_is_rejected(auth_client):
    assert auth_client.put("/api/settings/embeddings", json={"dimensions": 0}).status_code == 400


def test_a_stored_provider_outside_the_allowlist_reads_as_the_default(auth_client, session):
    """Defensive: a value written by an older build, or by hand, must not make the
    settings screen fail to load."""
    set_setting(session, TEST_USER, EMBEDDING_PROVIDER, "pinecone")

    assert auth_client.get("/api/settings/embeddings").json()["data"]["provider"] == PROVIDER_OPENAI


# ─── speech, which had no HTTP coverage either ───────────────────────────────


def test_speech_settings_round_trip(auth_client):
    auth_client.put(
        "/api/settings/speech", json={"deepgram_api_key": "dg-test", "deepgram_model": "nova-2"}
    )

    body = auth_client.get("/api/settings/speech").json()["data"]
    assert body["deepgram_key_configured"] is True
    assert body["deepgram_model"] == "nova-2"
    assert "dg-test" not in json.dumps(body)


# ─── auth ────────────────────────────────────────────────────────────────────


def test_settings_require_authentication(client):
    assert client.get("/api/settings/embeddings").status_code == 401
    assert client.put("/api/settings/embeddings", json={}).status_code == 401
