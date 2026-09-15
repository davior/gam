"""Turning text into vectors.

Provider-pluggable, the way gecko-notes treats its AI providers: the library that has to
be searched is the user's, and so is the decision about who gets to see it. OpenAI is
cheap and good; Ollama sends nothing off the machine, which for a library of private
material is not a minor preference.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session

from app.embeddings.base import Embedder, EmbeddingError
from app.embeddings.ollama import DEFAULT_MODEL as OLLAMA_DEFAULT_MODEL
from app.embeddings.ollama import OllamaEmbedder
from app.embeddings.openai import DEFAULT_DIMENSIONS, DEFAULT_MODEL as OPENAI_DEFAULT_MODEL
from app.embeddings.openai import OpenAIEmbedder

logger = logging.getLogger(__name__)

PROVIDER_OPENAI = "openai"
PROVIDER_OLLAMA = "ollama"

PROVIDERS = (PROVIDER_OPENAI, PROVIDER_OLLAMA)

__all__ = [
    "DEFAULT_DIMENSIONS",
    "Embedder",
    "EmbeddingError",
    "OLLAMA_DEFAULT_MODEL",
    "OPENAI_DEFAULT_MODEL",
    "PROVIDERS",
    "PROVIDER_OLLAMA",
    "PROVIDER_OPENAI",
    "build_embedder",
]


def build_embedder(session: Session, user_id: str) -> Optional[Embedder]:
    """This user's configured embedder, or None if they have not set one up.

    None rather than an exception: an unconfigured library is not broken, it just has no
    semantic search yet, and keyword search still works. Only an attempt to *use* it is
    an error worth raising.
    """
    from app.settings_store import (
        EMBEDDING_BASE_URL,
        EMBEDDING_DIMENSIONS,
        EMBEDDING_MODEL,
        EMBEDDING_PROVIDER,
        OLLAMA_BASE_URL,
        OPENAI_API_KEY,
        get_setting,
    )

    provider = get_setting(session, user_id, EMBEDDING_PROVIDER, PROVIDER_OPENAI)

    if provider == PROVIDER_OLLAMA:
        from app.embeddings.ollama import DEFAULT_BASE_URL

        return OllamaEmbedder(
            get_setting(session, user_id, OLLAMA_BASE_URL, DEFAULT_BASE_URL),
            model=get_setting(session, user_id, EMBEDDING_MODEL, OLLAMA_DEFAULT_MODEL),
        )

    api_key = get_setting(session, user_id, OPENAI_API_KEY)
    if not api_key:
        return None

    return OpenAIEmbedder(
        api_key,
        model=get_setting(session, user_id, EMBEDDING_MODEL, OPENAI_DEFAULT_MODEL),
        dimensions=int(get_setting(session, user_id, EMBEDDING_DIMENSIONS, DEFAULT_DIMENSIONS)),
        base_url=get_setting(session, user_id, EMBEDDING_BASE_URL) or None,
    )
