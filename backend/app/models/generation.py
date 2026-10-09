"""The catalogue of fal.ai endpoints a user can generate with (M8).

Global rather than per user, and data rather than code, because fal renames and retires
endpoint ids every few months — `imagen4/preview`, all of `veo3/*` and
`kling-video/v2.1` disappeared between May and October 2026. A list compiled into the
app would be wrong within one release; a table an admin can edit is wrong only until
somebody notices. Specified in docs/m8-ai-generation.md.

Each row also records its endpoint's *dialect*, because fal's endpoints agree on almost
nothing: the image goes in `image_url`, `image_urls[]` or `start_image_url`; the end
frame in `tail_image_url`, `end_image_url` or nowhere; a duration is `"5"`, `"8s"` or
`6`. Describing that per row is what lets one request builder serve every model rather
than growing a branch per endpoint.

Ported in spirit from gecko-notes' `ModelCatalogEntry`, minus its two defects: no
uniqueness on the endpoint id (here a unique index), and a "text to image" seed that was
really an edit endpoint and 422'd every call (here `kind` is checked against the image
fields, so that row cannot be saved).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.clock import utcnow

KIND_TEXT_TO_IMAGE = "text_to_image"
KIND_IMAGE_TO_IMAGE = "image_to_image"
KIND_IMAGE_TO_VIDEO = "image_to_video"

GENERATION_KINDS = (KIND_TEXT_TO_IMAGE, KIND_IMAGE_TO_IMAGE, KIND_IMAGE_TO_VIDEO)

# What a row's `unit_price` is per. The same words fal's pricing API uses.
PRICE_UNITS = ("image", "megapixel", "second", "video")


def new_generation_model_id() -> str:
    return str(uuid.uuid4())


class GenerationModel(SQLModel, table=True):
    id: str = Field(default_factory=new_generation_model_id, primary_key=True)
    # fal's id, all segments: `fal-ai/kling-video/v2.5-turbo/pro/image-to-video`. Unique,
    # so a duplicate is a 409 rather than two rows the form cannot tell apart.
    endpoint_id: str = Field(index=True, unique=True)
    kind: str = Field(index=True)
    label: str
    # Shown beside the model in the form: what it is good at, what it costs.
    note: str = Field(default="")
    sort_order: int = Field(default=0)
    # Hidden from the form rather than deleted, so a model fal has retired can be
    # switched off without losing the row an admin may want back.
    is_active: bool = Field(default=True)

    # The field the base image(s) go in. Null for text → image.
    image_field: Optional[str] = None
    # `image_urls: [...]` rather than `image_url: "..."`.
    image_field_is_list: bool = Field(default=False)
    # How many bases the endpoint takes. Enforced here because several models silently
    # truncate instead of refusing — Seedream keeps the *last* ten.
    max_images: int = Field(default=0)
    # Image → video only, and only for models that take a last frame.
    end_image_field: Optional[str] = None

    # JSON: the exact values the endpoint accepts for each user-facing choice —
    # {"aspect_ratios", "image_sizes", "durations", "resolutions"} — passed through
    # verbatim so `"5"`, `"8s"` and `6` all reach fal as the type it expects; plus
    # {"supports_seed", "supports_negative_prompt", "supports_audio", "max_outputs"}.
    # The form offers only what a row declares, and the server refuses anything else.
    options: str = Field(default="{}")
    # JSON: merged into every request body first, so a user's choices and the app's own
    # fields both override it. `{"generate_audio": false}` on Veo is the reason it
    # exists — its default is on, at half as much again per second.
    extra_params: str = Field(default="{}")

    # The list price, for when fal's pricing API cannot be reached with the user's key.
    # A cost computed from it is recorded as an estimate.
    unit_price: Optional[float] = None
    price_unit: Optional[str] = None  # one of PRICE_UNITS
    price_currency: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
