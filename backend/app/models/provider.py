"""A configured generative LLM, per user.

Separate from the embedding provider in `app.settings_store`, and deliberately so:
embeddings and generation are different capabilities with genuinely different provider
sets. Anthropic publishes no embeddings API at all, and DeepSeek's chat models are
text-only while its Anthropic-compatible endpoint is the one that can search the web.
`docs/m6-ai-enrichment.md` works through why one dial cannot serve both.

A row per provider rather than a key-value blob in `UserSetting`: a user keeps several
configured at once — a local Ollama for cheap work, Claude for anything needing vision —
and switches between them, which is what `is_active` is for.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.clock import utcnow


def new_provider_id() -> str:
    return str(uuid.uuid4())


class AIProvider(SQLModel, table=True):
    id: str = Field(default_factory=new_provider_id, primary_key=True)
    # Not Optional, unlike gecko-notes' column: there is no such thing as an
    # unowned provider here, and a nullable owner is a row every user can see.
    user_id: str = Field(index=True)

    name: str
    provider_type: str  # one of app.providers.PROVIDER_TYPES
    # Fernet-encrypted at rest (app.auth.encrypt_api_key), empty when the provider
    # needs no credential — Ollama is local and takes none.
    api_key: str = Field(default="")
    base_url: Optional[str] = None
    # Free text, not a closed list. Models appear faster than this app can be
    # redeployed, and a dropdown is the thing that dates first.
    model: str
    max_tokens: int = Field(default=16384)  # cap on the model's response (output) length

    # Whether this provider/model accepts image (and PDF) content blocks. Text-only
    # backends (DeepSeek chat, most local models) reject them with a deserialization
    # error from upstream, which is an unreadable way to learn the model cannot see.
    # This flag is what will gate the `describe` job in step 5 of the milestone.
    supports_images: bool = Field(default=False)

    # Speak the Anthropic Messages protocol to this provider instead of its default
    # (OpenAI-compatible) one. DeepSeek publishes an Anthropic-compatible endpoint at
    # api.deepseek.com/anthropic which runs the same server-side web_search tool Claude
    # does — so a DeepSeek provider with this set searches the web itself, natively,
    # with no third-party search key. Also usable for a `custom` Anthropic-compatible
    # gateway (with its own base_url). Ignored for `anthropic` (already native) and
    # `ollama` (its own protocol) — see app.providers.ANTHROPIC_CAPABLE_TYPES.
    use_anthropic_api: bool = Field(default=False)

    # Arbitrary extra request parameters, JSON-as-TEXT — e.g. {"temperature": 0}, top_p,
    # or provider-specific knobs. Merged into the outgoing request once the clients land
    # in step 2; structural keys (model, messages, max_tokens, …) are stripped first.
    # None sends nothing optional. Decoded to a dict on read.
    extra_params: Optional[str] = None

    enabled: bool = Field(default=True)
    # At most one active row per user. Enforced by the router rather than by a partial
    # unique index: SQLite supports one, Alembic's autogenerate does not emit it, and a
    # constraint the migration silently drops is worse than none.
    is_active: bool = Field(default=False)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
