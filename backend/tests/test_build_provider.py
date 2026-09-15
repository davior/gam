"""Choosing which client speaks to which row.

The decision keys off the protocol, never the vendor name — which is the whole reason
`speaks_anthropic` exists in one place — so these are the cases that would otherwise be
re-derived wrongly in each job.
"""

from app.auth import encrypt_api_key
from app.models.provider import AIProvider
from app.providers import build_provider
from app.providers.anthropic import AnthropicProvider
from app.providers.ollama import OllamaProvider
from app.providers.openai import OpenAIProvider

USER = "user-under-test"


def add(session, **overrides) -> AIProvider:
    fields = {
        "user_id": USER,
        "name": "Configured",
        "provider_type": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "api_key": encrypt_api_key("sk-secret"),
        "is_active": True,
        "enabled": True,
    }
    fields.update(overrides)
    provider = AIProvider(**fields)
    session.add(provider)
    session.commit()
    return provider


def test_no_provider_reads_as_none_not_an_error(session):
    """A library with no provider is not broken — it simply cannot be enriched yet, and
    everything else about it still works. Same contract as build_embedder."""
    assert build_provider(session, USER) is None


def test_a_row_that_is_not_active_is_not_used(session):
    add(session, is_active=False)

    assert build_provider(session, USER) is None


def test_a_disabled_row_is_not_used_even_when_active(session):
    """`enabled` is the user saying "not this one for now" without deleting it."""
    add(session, enabled=False)

    assert build_provider(session, USER) is None


def test_another_users_provider_is_not_borrowed(session):
    add(session, user_id="somebody-else")

    assert build_provider(session, USER) is None


def test_an_anthropic_row_speaks_messages(session):
    add(session)

    client = build_provider(session, USER)

    assert isinstance(client, AnthropicProvider)
    assert client.model == "claude-sonnet-4-20250514"
    assert client.provider_type == "anthropic"


def test_a_deepseek_row_with_the_flag_speaks_messages(session):
    add(session, provider_type="deepseek", model="deepseek-chat", use_anthropic_api=True)

    assert isinstance(build_provider(session, USER), AnthropicProvider)


def test_the_same_row_without_the_flag_speaks_openai(session):
    add(session, provider_type="deepseek", model="deepseek-chat")

    assert isinstance(build_provider(session, USER), OpenAIProvider)


def test_ollama_never_takes_the_messages_path(session):
    """Its address is the one this app allows to be private, so honouring the flag
    would aim a Messages request at an internal host."""
    add(session, provider_type="ollama", model="llama3.2", api_key="", use_anthropic_api=True)

    assert isinstance(build_provider(session, USER), OllamaProvider)


def test_ollama_needs_no_key(session):
    add(session, provider_type="ollama", model="llama3.2", api_key="")

    assert build_provider(session, USER) is not None


def test_every_other_type_needs_one(session):
    """A keyless hosted provider would 401 on every asset of a backfill in turn."""
    add(session, provider_type="openai", model="gpt-4o", api_key="")

    assert build_provider(session, USER) is None


def test_a_key_that_no_longer_decrypts_reads_as_unconfigured(session):
    """JWT_SECRET_KEY rotated under the row. The settings screen already reports the
    key as missing; this makes the enrichment side agree with it."""
    add(session, api_key="enc:this-is-not-a-valid-fernet-token")

    assert build_provider(session, USER) is None


def test_the_active_row_wins_over_the_others(session):
    add(session, name="Old", provider_type="openai", model="gpt-4o", is_active=False)
    add(session, name="Current", provider_type="ollama", model="llama3.2", api_key="", is_active=True)

    client = build_provider(session, USER)

    assert isinstance(client, OllamaProvider)
    assert client.model == "llama3.2"


def test_the_client_carries_the_stored_key(session, monkeypatch):
    """Decrypted once here rather than in each job."""
    import httpx

    from app.providers import _upstream

    seen = {}

    def fake_post_json(url, *, headers=None, json_body, timeout, label, **kwargs):
        seen.update(headers or {})
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "ok"}], "usage": {}},
        )

    monkeypatch.setattr(_upstream, "post_json", fake_post_json)
    add(session)

    build_provider(session, USER).complete("Hi")

    assert seen["x-api-key"] == "sk-secret"
