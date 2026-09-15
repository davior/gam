"""Wire shapes for the AI provider CRUD.

The one rule this file exists to enforce: a stored key goes out of here as a boolean and
never as a string. `app/routers/settings.py` already states it for the Deepgram and
embedding credentials — "the response says whether a key is configured, not what it is" —
and a provider key is no different.

gecko-notes redacts its `api_key` to `""` on read, which is indistinguishable from "no
key set": its own settings screen cannot tell you whether a provider is configured. Hence
`api_key_configured` here.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AIProviderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: str
    name: str
    provider_type: str
    base_url: Optional[str] = None
    model: str
    max_tokens: int
    supports_images: bool
    use_anthropic_api: bool
    extra_params: Optional[dict[str, Any]] = None
    enabled: bool
    is_active: bool
    # Derived in the router from the stored column, which is never serialised.
    api_key_configured: bool = False

    @field_validator("extra_params", mode="before")
    @classmethod
    def _decode_extra_params(cls, value: Any) -> Any:
        """JSON-as-TEXT in the column, a dict on the wire.

        Unreadable text reads as "nothing configured" rather than raising: a row written
        by an older version should not make the whole settings screen 500.
        """
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except ValueError:
                return None
            return decoded if isinstance(decoded, dict) else None
        return value


class AIProviderCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    name: str = Field(min_length=1, max_length=200)
    provider_type: str
    api_key: str = ""
    base_url: Optional[str] = None
    model: str = Field(min_length=1, max_length=200)
    max_tokens: int = Field(default=16384, ge=1, le=200000)
    supports_images: bool = False
    use_anthropic_api: bool = False
    extra_params: Optional[dict[str, Any]] = None
    enabled: bool = True
    is_active: bool = False


class AIProviderUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    provider_type: Optional[str] = None
    # Three-state, the same contract as every other credential in this app: None leaves
    # the stored key alone, "" removes it, anything else replaces it. gecko-notes writes
    # only on a truthy value, which is why a key can never be cleared there.
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = Field(default=None, min_length=1, max_length=200)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=200000)
    supports_images: Optional[bool] = None
    use_anthropic_api: Optional[bool] = None
    extra_params: Optional[dict[str, Any]] = None
    enabled: Optional[bool] = None
    is_active: Optional[bool] = None


class AIProviderTest(BaseModel):
    """A connection probe, for a saved provider or for an unsaved form.

    `provider_id` names a saved row whose stored key should be used — so the screen can
    re-test a provider without asking the user to paste the key again, which it could not
    do anyway since it never received it.
    """

    model_config = ConfigDict(protected_namespaces=())

    provider_id: Optional[str] = None
    provider_type: str
    api_key: str = ""
    base_url: Optional[str] = None
    model: str = Field(min_length=1, max_length=200)
    use_anthropic_api: bool = False


class ProviderTestResult(BaseModel):
    success: bool
    message: str
