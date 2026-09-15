"""Generative LLM providers.

Constants and endpoint resolution live here; the clients that speak to those endpoints
arrive in step 2 of the milestone (`docs/m6-ai-enrichment.md`). Kept import-light on
purpose — `app.routers.providers` needs the allowlist and nothing else, and pulling an
HTTP client in for a settings write would be paying for a connection nobody opened.
"""

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

__all__ = [
    "ANTHROPIC_CAPABLE_TYPES",
    "CHECKED_BASE_URL_TYPES",
    "PROVIDER_ANTHROPIC",
    "PROVIDER_CUSTOM",
    "PROVIDER_DEEPSEEK",
    "PROVIDER_OLLAMA",
    "PROVIDER_OPENAI",
    "PROVIDER_TYPES",
]
