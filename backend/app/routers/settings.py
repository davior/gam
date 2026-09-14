"""Per-user settings.

A secret is written but never read back: the response says whether a key is configured,
not what it is. There is no path by which a stored credential reaches the browser.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.embeddings import PROVIDER_OLLAMA, PROVIDER_OPENAI, PROVIDERS
from app.embeddings.ollama import DEFAULT_BASE_URL
from app.embeddings.ollama import DEFAULT_MODEL as OLLAMA_DEFAULT_MODEL
from app.embeddings.openai import DEFAULT_DIMENSIONS
from app.embeddings.openai import DEFAULT_MODEL as OPENAI_DEFAULT_MODEL
from app.enrichment.deepgram import DEFAULT_MODEL
from app.schemas import DataResponse
from app.settings_store import (
    DEEPGRAM_API_KEY,
    DEEPGRAM_MODEL,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    EMBEDDING_PROVIDER,
    OLLAMA_BASE_URL,
    OPENAI_API_KEY,
    clear_setting,
    get_setting,
    has_setting,
    set_setting,
)

router = APIRouter()

# Offered in the UI. Kept to models this app has actually been built against rather
# than everything Deepgram publishes.
DEEPGRAM_MODELS = [
    {"id": "nova-3", "label": "Nova 3 (recommended)"},
    {"id": "nova-2", "label": "Nova 2"},
]


class SpeechSettings(BaseModel):
    deepgram_key_configured: bool
    deepgram_model: str
    available_models: list[dict] = Field(default_factory=lambda: DEEPGRAM_MODELS)


class SpeechSettingsUpdate(BaseModel):
    # None means "leave it alone"; "" means "remove it". Without that distinction
    # there is no way to clear a key once set.
    deepgram_api_key: str | None = None
    deepgram_model: str | None = None


@router.get("/speech", response_model=DataResponse[SpeechSettings])
def read_speech_settings(
    user: CurrentUser, session: Session = Depends(get_session)
) -> DataResponse[SpeechSettings]:
    return DataResponse(
        data=SpeechSettings(
            deepgram_key_configured=has_setting(session, user.id, DEEPGRAM_API_KEY),
            deepgram_model=get_setting(session, user.id, DEEPGRAM_MODEL, DEFAULT_MODEL),
        )
    )


@router.put("/speech", response_model=DataResponse[SpeechSettings])
def write_speech_settings(
    payload: SpeechSettingsUpdate,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[SpeechSettings]:
    if payload.deepgram_api_key is not None:
        if payload.deepgram_api_key.strip():
            set_setting(session, user.id, DEEPGRAM_API_KEY, payload.deepgram_api_key.strip())
        else:
            clear_setting(session, user.id, DEEPGRAM_API_KEY)

    if payload.deepgram_model:
        set_setting(session, user.id, DEEPGRAM_MODEL, payload.deepgram_model)

    return read_speech_settings(user, session)


# ─── embeddings ──────────────────────────────────────────────────────────────
#
# Semantic search is off until one of these is configured, and until this endpoint
# existed there was no way to configure it: the keys were defined in settings_store and
# read by build_embedder, but nothing could write them. Search silently ran keyword-only
# while the UI told the user to "add an embedding provider in Settings".

# The default model differs per provider, so "what should the model box say" cannot be a
# constant. Dimensions are OpenAI-only: Ollama's model decides its own width.
EMBEDDING_MODELS = {
    PROVIDER_OPENAI: [
        {"id": "text-embedding-3-small", "label": "text-embedding-3-small (recommended)"},
        {"id": "text-embedding-3-large", "label": "text-embedding-3-large"},
    ],
    PROVIDER_OLLAMA: [
        {"id": "nomic-embed-text", "label": "nomic-embed-text (recommended)"},
        {"id": "mxbai-embed-large", "label": "mxbai-embed-large"},
    ],
}

DEFAULT_MODEL_FOR = {
    PROVIDER_OPENAI: OPENAI_DEFAULT_MODEL,
    PROVIDER_OLLAMA: OLLAMA_DEFAULT_MODEL,
}


class EmbeddingSettings(BaseModel):
    provider: str
    model: str
    dimensions: int
    openai_key_configured: bool
    ollama_base_url: str
    # True when the current provider has everything it needs. The UI uses this to say
    # whether semantic search is actually on, which "key configured" alone cannot
    # answer — Ollama needs no key at all.
    configured: bool
    available_providers: list[str] = Field(default_factory=lambda: list(PROVIDERS))
    available_models: list[dict] = Field(default_factory=list)


class EmbeddingSettingsUpdate(BaseModel):
    provider: str | None = None
    model: str | None = None
    dimensions: int | None = None
    # None means "leave it alone"; "" means "remove it" — the same contract as the
    # Deepgram key above, and for the same reason: without the distinction a stored key
    # can never be cleared.
    openai_api_key: str | None = None
    ollama_base_url: str | None = None


def _embedding_settings(session: Session, user_id: str) -> EmbeddingSettings:
    provider = get_setting(session, user_id, EMBEDDING_PROVIDER, PROVIDER_OPENAI)
    if provider not in PROVIDERS:
        provider = PROVIDER_OPENAI

    key_configured = has_setting(session, user_id, OPENAI_API_KEY)

    return EmbeddingSettings(
        provider=provider,
        model=get_setting(session, user_id, EMBEDDING_MODEL, DEFAULT_MODEL_FOR[provider]),
        dimensions=int(get_setting(session, user_id, EMBEDDING_DIMENSIONS, DEFAULT_DIMENSIONS)),
        openai_key_configured=key_configured,
        ollama_base_url=get_setting(session, user_id, OLLAMA_BASE_URL, DEFAULT_BASE_URL),
        configured=key_configured if provider == PROVIDER_OPENAI else True,
        available_models=EMBEDDING_MODELS[provider],
    )


@router.get("/embeddings", response_model=DataResponse[EmbeddingSettings])
def read_embedding_settings(
    user: CurrentUser, session: Session = Depends(get_session)
) -> DataResponse[EmbeddingSettings]:
    return DataResponse(data=_embedding_settings(session, user.id))


@router.put("/embeddings", response_model=DataResponse[EmbeddingSettings])
def write_embedding_settings(
    payload: EmbeddingSettingsUpdate,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[EmbeddingSettings]:
    if payload.provider is not None:
        if payload.provider not in PROVIDERS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "bad_request",
                    "message": f"Unknown embedding provider: {payload.provider}",
                },
            )
        previous = get_setting(session, user.id, EMBEDDING_PROVIDER, PROVIDER_OPENAI)
        set_setting(session, user.id, EMBEDDING_PROVIDER, payload.provider)
        # Switching provider carries the old provider's model name across, which would
        # ask OpenAI for "nomic-embed-text". Reset to the new provider's default unless
        # this same request names a model.
        if payload.provider != previous and payload.model is None:
            set_setting(session, user.id, EMBEDDING_MODEL, DEFAULT_MODEL_FOR[payload.provider])

    if payload.model:
        set_setting(session, user.id, EMBEDDING_MODEL, payload.model)

    if payload.dimensions is not None:
        if payload.dimensions < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "bad_request", "message": "Dimensions must be positive"},
            )
        set_setting(session, user.id, EMBEDDING_DIMENSIONS, payload.dimensions)

    if payload.openai_api_key is not None:
        if payload.openai_api_key.strip():
            set_setting(session, user.id, OPENAI_API_KEY, payload.openai_api_key.strip())
        else:
            clear_setting(session, user.id, OPENAI_API_KEY)

    if payload.ollama_base_url is not None:
        if payload.ollama_base_url.strip():
            set_setting(session, user.id, OLLAMA_BASE_URL, payload.ollama_base_url.strip())
        else:
            clear_setting(session, user.id, OLLAMA_BASE_URL)

    return DataResponse(data=_embedding_settings(session, user.id))
