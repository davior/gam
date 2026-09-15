"""Which URL a provider is actually addressed at, and over which protocol.

Ported from gecko-notes (`backend/app/routers/settings.py:843-886`), whose reasoning is
the part worth carrying: three of the decisions here look arbitrary and are not.

The Messages protocol is no longer only Anthropic's. DeepSeek publishes an
Anthropic-compatible endpoint at api.deepseek.com/anthropic, and — this is the point of
it — that endpoint runs the same SERVER-SIDE web_search tool Claude does. So a DeepSeek
provider pointed at it searches the web natively, exactly the way a Claude one does: no
third-party search key, no per-search fee, nothing for this app to run. Its
OpenAI-compatible endpoint has no such tool.

A provider opts in per row with `use_anthropic_api`; `anthropic` itself is always on this
path. Everything downstream keys off `speaks_anthropic()`, not off the vendor name.
"""

from __future__ import annotations

from typing import Optional

from app.models.provider import AIProvider
from app.providers import (
    ANTHROPIC_CAPABLE_TYPES,
    PROVIDER_ANTHROPIC,
    PROVIDER_DEEPSEEK,
)

ANTHROPIC_BASE = "https://api.anthropic.com"
DEEPSEEK_ANTHROPIC_BASE = "https://api.deepseek.com/anthropic"
OPENAI_BASE = "https://api.openai.com"
DEEPSEEK_BASE = "https://api.deepseek.com"
OLLAMA_BASE = "http://localhost:11434"


def speaks_anthropic(provider: AIProvider) -> bool:
    """Whether this provider is addressed over the Anthropic Messages protocol."""
    if provider.provider_type == PROVIDER_ANTHROPIC:
        return True
    return bool(provider.use_anthropic_api) and provider.provider_type in ANTHROPIC_CAPABLE_TYPES


def anthropic_base(provider: AIProvider) -> str:
    """Base URL for a provider's Messages endpoint.

    Anthropic's own is fixed, and so is DeepSeek's — its stored `base_url` describes the
    OpenAI-compatible endpoint, so it is deliberately ignored here (the same reasoning as
    `openai_compat_base`: a fixed managed endpoint spares the user a field and stops a
    crafted base_url redirecting the request). Any other provider opting in supplies its
    own gateway URL, SSRF-checked when it was saved.
    """
    if provider.provider_type == PROVIDER_ANTHROPIC:
        return ANTHROPIC_BASE
    if provider.provider_type == PROVIDER_DEEPSEEK:
        return DEEPSEEK_ANTHROPIC_BASE
    return (provider.base_url or ANTHROPIC_BASE).rstrip("/")


def openai_compat_base(provider_type: Optional[str], base_url: Optional[str]) -> str:
    """Resolve the upstream base URL for an OpenAI-compatible provider.

    DeepSeek is a fixed managed endpoint, like OpenAI's own, so it ignores any stored
    base_url: that both spares the user a URL field and stops a crafted base_url from
    redirecting the request. `openai` and `custom` supply their own, already SSRF-checked
    on save.
    """
    if provider_type == PROVIDER_DEEPSEEK:
        return DEEPSEEK_BASE
    return (base_url or OPENAI_BASE).rstrip("/")


def anthropic_headers(api_key: str, provider_type: str) -> dict[str, str]:
    """Auth and protocol headers for one Messages request.

    Beta flags go only to Anthropic: they name Anthropic-internal features, and a gateway
    that does not recognise one could reject the whole request over a feature it was
    never asked for. Compatible gateways get BOTH auth headers because they disagree
    about which to read — DeepSeek documents `x-api-key`, while Claude Code's own
    ANTHROPIC_AUTH_TOKEN path sends a bearer token — and an unread header is ignored.
    """
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if provider_type == PROVIDER_ANTHROPIC:
        headers["anthropic-beta"] = "pdfs-2024-09-25"
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers
