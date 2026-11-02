"""seed the FLUX Pro text-to-image models

The catalogue offered only FLUX.1 [schnell] and [dev] for text to image, and schnell
sorted first. Video Creator takes the first model of a kind when nobody names one, so
every picture its assistant made came from the draft model. These three rows are the
quality tier, and they sort ahead of schnell (sort_order 5–7 against its 10) so that a
user who has set no default (see `default_generation_models` in app/settings_store.py)
starts from Pro rather than from a draft.

The rows are a literal copy, not an import, for the reason the first seed migration
(`a8e1c0f4d2b6`) gives. Field names and enums were checked against the endpoint input
types generated from fal's OpenAPI schemas in `@fal-ai/client` 1.11.0-alpha.6
(`src/types/endpoints.d.ts`, 2026-11-02); the evidence for each row is beside it.

A row whose endpoint is already in the catalogue (an admin added it by hand) is left
alone, and the downgrade removes only rows this revision inserted and nobody has edited
since, so it never deletes an admin's work.

Revision ID: b7d41e9c2a60
Revises: aecf97cd87b1
Create Date: 2026-11-02 12:00:00.000000+00:00
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d41e9c2a60'
down_revision: Union[str, None] = 'aecf97cd87b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _options(*, aspect_ratios=(), image_sizes=(), max_outputs=1, custom_size=None) -> str:
    return json.dumps(
        {
            "aspect_ratios": list(aspect_ratios),
            "image_sizes": list(image_sizes),
            "durations": [],
            "resolutions": [],
            "supports_seed": True,
            "supports_negative_prompt": False,
            "supports_audio": False,
            "max_outputs": max_outputs,
            "custom_size": custom_size,
        }
    )


_FLUX_SIZES = (
    "square_hd", "square", "portrait_4_3", "portrait_16_9", "landscape_4_3", "landscape_16_9",
)

SEEDS = [
    dict(
        endpoint_id="fal-ai/flux-pro/v1.1-ultra",
        label="FLUX1.1 [pro] ultra",
        note="Highest quality, up to about 4 megapixels. Sizes by aspect ratio.",
        sort_order=5,
        # FluxProV11UltraInput: `aspect_ratio` 21:9…9:21 (no presets, no width/height),
        # `num_images`, `seed`, plus `raw` and `safety_tolerance`, which an admin can pin
        # in extra_params. Its `image_url` is an image *prompt*, not a base to edit, so
        # the row stays text to image.
        options=_options(
            aspect_ratios=("21:9", "16:9", "4:3", "3:2", "1:1", "2:3", "3:4", "9:16", "9:21"),
            max_outputs=4,
        ),
        # Price: fal's published list price, per image at any ratio. Only the fallback:
        # the job asks fal's pricing API first.
        unit_price=0.06,
        price_unit="image",
    ),
    dict(
        endpoint_id="fal-ai/flux-2-pro",
        label="FLUX.2 [pro]",
        note="Newest FLUX. Follows long prompts closely and renders text well.",
        sort_order=6,
        # Flux2ProInput (= Flux2MaxInput): `image_size` preset or {width, height}, `seed`,
        # `safety_tolerance`. No `num_images`, so one picture a run.
        options=_options(
            image_sizes=_FLUX_SIZES,
            max_outputs=1,
            # About 4 megapixels, the most fal documents for FLUX.2. The schema states no
            # bounds; these are not checked against fal, which could not be reached when
            # this was written. An admin can change them on the row.
            custom_size={"max_width": 2048, "max_height": 2048, "multiple_of": 16,
                         "max_pixels": 4_194_304},
        ),
        # Price: fal bills $0.03 for the first megapixel and less for each after, so a
        # flat $0.03 a megapixel overstates a large picture a little.
        unit_price=0.03,
        price_unit="megapixel",
    ),
    dict(
        endpoint_id="fal-ai/flux-pro/v1.1",
        label="FLUX1.1 [pro]",
        note="Fast, sharp FLUX Pro at up to 1440 pixels a side.",
        sort_order=7,
        # FluxProV11Input: `image_size` preset or {width, height}, `num_images`, `seed`,
        # `safety_tolerance`.
        options=_options(
            image_sizes=_FLUX_SIZES,
            max_outputs=4,
            # Black Forest Labs' documented range for FLUX1.1 [pro]: 256–1440 a side in
            # steps of 32. Not checked against fal, for the reason above.
            custom_size={"max_width": 1440, "max_height": 1440, "multiple_of": 32,
                         "max_pixels": None},
        ),
        # Price: fal's published list price; the fallback, as above.
        unit_price=0.04,
        price_unit="megapixel",
    ),
]

_COLUMNS = (
    "id", "endpoint_id", "kind", "label", "note", "sort_order", "is_active", "image_field",
    "image_field_is_list", "max_images", "end_image_field", "options", "extra_params",
    "unit_price", "price_unit", "price_currency", "created_at", "updated_at",
)


def upgrade() -> None:
    conn = op.get_bind()
    existing = {
        row[0]
        for row in conn.execute(sa.text("SELECT endpoint_id FROM generationmodel")).fetchall()
    }
    # Naive UTC, as every timestamp here is (app/clock.py), spelled out rather than
    # imported for the reason the module docstring gives.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    insert = sa.text(
        f"INSERT INTO generationmodel ({', '.join(_COLUMNS)}) "
        f"VALUES ({', '.join(':' + c for c in _COLUMNS)})"
    )
    for seed in SEEDS:
        if seed["endpoint_id"] in existing:
            continue
        conn.execute(insert, {
            "id": str(uuid.uuid4()),
            "kind": "text_to_image",
            "is_active": True,
            "image_field": None,
            "image_field_is_list": False,
            "max_images": 0,
            "end_image_field": None,
            "extra_params": "{}",
            "price_currency": "USD",
            "created_at": now,
            "updated_at": now,
            **seed,
        })


def downgrade() -> None:
    # Only rows nobody has saved since: an admin's edit bumps updated_at past
    # created_at, and a row added by hand before this revision has other options.
    conn = op.get_bind()
    for seed in SEEDS:
        conn.execute(
            sa.text(
                "DELETE FROM generationmodel WHERE endpoint_id = :endpoint_id "
                "AND options = :options AND label = :label AND updated_at = created_at"
            ),
            {"endpoint_id": seed["endpoint_id"], "options": seed["options"],
             "label": seed["label"]},
        )
