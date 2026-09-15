"""Generative LLM providers.

Constants and endpoint resolution live here, alongside `build_provider` — the one
function a caller needs. The clients themselves are imported lazily inside it, so the
settings router still pays nothing for an HTTP stack it never uses, and `endpoints.py`
can import these constants without a cycle.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlmodel import Session

    from app.models.provider import AIProvider
    from app.providers.base import LLMProvider

logger = logging.getLogger(__name__)

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_OLLAMA = "ollama"
PROVIDER_CUSTOM = "custom"

# The allowlist the router validates against, mirroring app.embeddings.PROVIDERS. A
# stored value outside it is not a 500 later — it is a 400 now.
PROVIDER_TYPES = (
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    PROVIDER_DEEPSEEK,
    PROVIDER_OLLAMA,
    PROVIDER_CUSTOM,
)

# Types that may be pointed at an Anthropic-compatible endpoint instead of their own.
# `anthropic` is absent because it is always on that path already. `ollama` is absent
# deliberately: it speaks only its own protocol, and its base_url is the one address in
# this app allowed to be private, so honouring the flag there would aim the Messages
# request at an internal host.
ANTHROPIC_CAPABLE_TYPES = (PROVIDER_DEEPSEEK, PROVIDER_CUSTOM)

# Types whose base_url is user-supplied and therefore SSRF-checked before it is stored.
# `anthropic` and `deepseek` resolve to fixed managed endpoints (see endpoints.py) and
# `ollama` is the deliberate private-address exception.
CHECKED_BASE_URL_TYPES = (PROVIDER_OPENAI, PROVIDER_CUSTOM)

def active_provider(session: "Session", user_id: str) -> Optional["AIProvider"]:
    """This user's chosen provider row, if they have one that can run.

    `enabled` is the user saying "not this one for now" without deleting it, so a
    disabled row is not a candidate even when it is the active one.
    """
    from sqlmodel import col, select

    from app.models.provider import AIProvider

    # `col(...).is_(True)` rather than `== True`: this has to render as SQL, and the
    # plain comparison is the one flake8 flags and readers misread as a Python bug.
    return session.exec(
        select(AIProvider).where(
            AIProvider.user_id == user_id,
            col(AIProvider.is_active).is_(True),
            col(AIProvider.enabled).is_(True),
        )
    ).first()


def build_provider(session: "Session", user_id: str) -> Optional["LLMProvider"]:
    """This user's configured LLM client, or None if they have not set one up.

    None rather than an exception, exactly as `build_embedder` does: a library with no
    provider is not broken, it simply cannot be enriched yet, and everything else about
    it still works. Only an attempt to *use* one is an error worth raising.

    Which client is chosen keys off the protocol the provider speaks, never off the
    vendor name — a DeepSeek row with `use_anthropic_api` is addressed over Messages,
    and `speaks_anthropic` is the single place that decision lives.
    """
    from app.auth import decrypt_api_key
    from app.providers.endpoints import speaks_anthropic

    row = active_provider(session, user_id)
    if row is None:
        return None

    api_key = decrypt_api_key(row.api_key) if row.api_key else ""
    if not api_key and row.provider_type != PROVIDER_OLLAMA:
        # A row whose key failed to decrypt — JWT_SECRET_KEY rotated under it — reads as
        # unconfigured rather than as a provider that will 401 on every asset in a
        # backfill. The settings screen already reports the key as missing.
        logger.warning("Provider %s for %s has no usable API key", row.id, user_id)
        return None

    if speaks_anthropic(row):
        from app.providers.anthropic import AnthropicProvider

        return AnthropicProvider(row, api_key)

    if row.provider_type == PROVIDER_OLLAMA:
        from app.providers.ollama import OllamaProvider

        return OllamaProvider(row)

    from app.providers.openai import OpenAIProvider

    return OpenAIProvider(row, api_key)


__all__ = [
    "ANTHROPIC_CAPABLE_TYPES",
    "CHECKED_BASE_URL_TYPES",
    "PROVIDER_ANTHROPIC",
    "PROVIDER_CUSTOM",
    "PROVIDER_DEEPSEEK",
    "PROVIDER_OLLAMA",
    "PROVIDER_OPENAI",
    "PROVIDER_TYPES",
    "active_provider",
    "build_provider",
]
