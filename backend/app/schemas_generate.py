"""Wire shapes for generation (M8): the model catalogue and a generation request.

The contract is docs/m8-ai-generation.md's API section, which the frontend is built
against field for field.

Most fields on the write shapes are typed loosely on purpose. A missing label or an
`options` that is a string should come back as `invalid_model_entry` with a sentence
naming the field — the house error shape — rather than as FastAPI's validation list,
which the frontend's one error handler cannot read. The real checks are in
`app/generation/catalogue.py` and `app/generation/request.py`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class GenerationModelRead(BaseModel):
    id: str
    endpoint_id: str
    kind: str
    label: str
    note: str = ""
    sort_order: int = 0
    is_active: bool = True
    image_field: Optional[str] = None
    image_field_is_list: bool = False
    max_images: int = 0
    end_image_field: Optional[str] = None
    # Decoded from the JSON-as-TEXT columns, every option key present.
    options: dict[str, Any] = Field(default_factory=dict)
    extra_params: dict[str, Any] = Field(default_factory=dict)
    unit_price: Optional[float] = None
    price_unit: Optional[str] = None
    price_currency: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class GenerationModelCreate(BaseModel):
    endpoint_id: Optional[str] = None
    kind: Optional[str] = None
    label: Optional[str] = None
    note: str = ""
    sort_order: int = 0
    is_active: bool = True
    image_field: Optional[str] = None
    image_field_is_list: bool = False
    max_images: int = 0
    end_image_field: Optional[str] = None
    options: Any = Field(default_factory=dict)
    extra_params: Any = Field(default_factory=dict)
    unit_price: Optional[float] = None
    price_unit: Optional[str] = None
    price_currency: Optional[str] = None


class GenerationModelUpdate(BaseModel):
    """Every field optional, and *present* distinguished from *absent*.

    The router reads `model_fields_set`, so `{"note": null}` clears the note while `{}`
    leaves it alone — the distinction gecko-notes' catalogue PUT lost by skipping every
    None, which is why nothing there could ever be cleared.
    """

    endpoint_id: Optional[str] = None
    kind: Optional[str] = None
    label: Optional[str] = None
    note: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None
    image_field: Optional[str] = None
    image_field_is_list: Optional[bool] = None
    max_images: Optional[int] = None
    end_image_field: Optional[str] = None
    options: Any = None
    extra_params: Any = None
    unit_price: Optional[float] = None
    price_unit: Optional[str] = None
    price_currency: Optional[str] = None


class GenerationParams(BaseModel):
    # Any, not str: a choice is matched against the row's declared values with its
    # type, and Veo's "8s", Kling's "5" and Wan's 6 are all real durations.
    aspect_ratio: Any = None
    image_size: Any = None
    duration: Any = None
    resolution: Any = None
    seed: Optional[int] = None
    negative_prompt: Optional[str] = None
    generate_audio: Optional[bool] = None
    num_outputs: int = 1


class GenerationCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_id: str = ""
    prompt: str = ""
    base_asset_ids: list[str] = Field(default_factory=list)
    end_frame_asset_id: Optional[str] = None
    params: GenerationParams = Field(default_factory=GenerationParams)


class RegenerateRequest(BaseModel):
    # None keeps the stored prompt.
    prompt: Optional[str] = None
    # Resend the seed fal reported, so the same model makes the same picture again
    # with only what was changed changed. Off, the seed is dropped and fal picks one.
    reuse_seed: bool = False


class GenerationSettings(BaseModel):
    fal_key_configured: bool


class GenerationSettingsUpdate(BaseModel):
    # None leaves the stored key alone; "" removes it — the contract every credential
    # in this app follows, since without it a key could never be cleared.
    fal_api_key: Optional[str] = None
