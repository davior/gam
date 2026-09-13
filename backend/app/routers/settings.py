"""Per-user settings.

A secret is written but never read back: the response says whether a key is configured,
not what it is. There is no path by which a stored credential reaches the browser.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.enrichment.deepgram import DEFAULT_MODEL
from app.schemas import DataResponse
from app.settings_store import (
    DEEPGRAM_API_KEY,
    DEEPGRAM_MODEL,
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
