"""add ai generation: asset provenance, usage external_ref, the model catalogue

M8, in one revision, as docs/m8-ai-generation.md specifies:

1. Seven nullable `ai_*` columns on `asset` — what made a generated file, kept so it can
   be made again. No indexes and no foreign keys: the bases live as ids inside
   `ai_source_assets`, so deleting one never blocks or breaks what was made from it.
   Adding columns is plain `ALTER TABLE ADD COLUMN` on SQLite, so the upgrade does not
   recreate `asset`; the downgrade's drops do, hence the PRAGMA dance `7d4b9c1a6f28`
   explains.
2. `usageevent.external_ref` — fal's `x-fal-request-id`, so a recorded cost can be found
   in fal's own billing.
3. `generationmodel` — the admin-managed endpoint catalogue, with its endpoint id unique.
4. Nine seed rows, three per kind, so a fresh install can generate before anybody has
   been made an admin.

The seed is a literal copy rather than an import of app code: a migration describes the
data as it was when it ran, and must not change meaning when the app does. Every field
name and allowed value below was checked against the endpoint input types generated
from fal's OpenAPI schemas in `@fal-ai/client` 1.11.0-alpha.5 (`src/types/endpoints.d.ts`,
2026-10-06); the evidence for each row is beside it. Prices are list prices and only the
fallback — the job asks fal's pricing API first.

Revision ID: a8e1c0f4d2b6
Revises: 9c2e08b4a1f7
Create Date: 2026-10-07 09:00:00.000000+00:00
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'a8e1c0f4d2b6'
down_revision: Union[str, None] = '9c2e08b4a1f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _options(
    *,
    aspect_ratios=(),
    image_sizes=(),
    durations=(),
    resolutions=(),
    supports_seed=False,
    supports_negative_prompt=False,
    supports_audio=False,
    max_outputs=1,
) -> str:
    return json.dumps(
        {
            "aspect_ratios": list(aspect_ratios),
            "image_sizes": list(image_sizes),
            "durations": list(durations),
            "resolutions": list(resolutions),
            "supports_seed": supports_seed,
            "supports_negative_prompt": supports_negative_prompt,
            "supports_audio": supports_audio,
            "max_outputs": max_outputs,
        }
    )


# FluxDevInput (FluxSchnellInput is an alias of it): `image_size` is a preset or
# {width, height}; `num_images`, `seed`; no `aspect_ratio`, no `negative_prompt`.
_FLUX_SIZES = (
    "square_hd", "square", "portrait_4_3", "portrait_16_9", "landscape_4_3", "landscape_16_9",
)

SEEDS = [
    # ─── text → image ────────────────────────────────────────────────────────
    dict(
        endpoint_id="fal-ai/flux/schnell",
        kind="text_to_image",
        label="FLUX.1 [schnell]",
        note="Fastest and cheapest. Good for drafts.",
        sort_order=10,
        options=_options(image_sizes=_FLUX_SIZES, supports_seed=True, max_outputs=4),
        # Price: LiteLLM's fal table.
        unit_price=0.003,
        price_unit="megapixel",
    ),
    dict(
        endpoint_id="fal-ai/flux/dev",
        kind="text_to_image",
        label="FLUX.1 [dev]",
        note="Good quality at a low price.",
        sort_order=20,
        options=_options(image_sizes=_FLUX_SIZES, supports_seed=True, max_outputs=4),
        # Price: the example in fal's own pricing API reference.
        unit_price=0.025,
        price_unit="megapixel",
    ),
    dict(
        endpoint_id="fal-ai/nano-banana-2",
        kind="text_to_image",
        label="Nano Banana 2",
        note="Google's model. Strong at text in images. Price rises with resolution.",
        sort_order=30,
        # NanoBanana2Input: `aspect_ratio` incl. auto and the extreme 4:1…1:8,
        # `resolution` "0.5K"|"1K"|"2K"|"4K", `num_images`, `seed`. Its output reports no
        # seed back.
        options=_options(
            aspect_ratios=(
                "auto", "21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3",
                "9:16", "4:1", "1:4", "8:1", "1:8",
            ),
            resolutions=("0.5K", "1K", "2K", "4K"),
            supports_seed=True,
            max_outputs=4,
        ),
        # Price: LiteLLM, at 1K.
        unit_price=0.08,
        price_unit="image",
    ),
    # ─── image → image ───────────────────────────────────────────────────────
    dict(
        endpoint_id="fal-ai/nano-banana/edit",
        kind="image_to_image",
        label="Nano Banana (edit)",
        note="Edits or combines images from an instruction.",
        sort_order=10,
        # NanoBananaEditInput: `image_urls: Array<string>` (required), `aspect_ratio`
        # incl. auto, `num_images`, `seed`. The schema states no cap on the list; three
        # is what Google advises for this model, and an admin can raise it.
        image_field="image_urls",
        image_field_is_list=True,
        max_images=3,
        options=_options(
            aspect_ratios=(
                "auto", "21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3",
                "9:16",
            ),
            supports_seed=True,
            max_outputs=4,
        ),
        # Price: LiteLLM's text-to-image figure for the same model.
        unit_price=0.039,
        price_unit="image",
    ),
    dict(
        endpoint_id="fal-ai/bytedance/seedream/v4.5/edit",
        kind="image_to_image",
        label="Seedream 4.5 (edit)",
        note="Takes up to ten reference images.",
        sort_order=20,
        # SeedDream45EditInput: `image_urls` — "up to 10 image inputs are allowed. If over
        # 10 images are sent, only the last 10 will be used" — so ten, enforced here
        # rather than silently truncated there. `image_size` accepts the presets in its
        # type, but its doc says width and height must be 1920–4096 (or 2560×1440 to
        # 4096² in total), which every preset is below; only the auto sizes are safe.
        # `num_images`, `seed`.
        image_field="image_urls",
        image_field_is_list=True,
        max_images=10,
        options=_options(
            image_sizes=("auto_2K", "auto_4K"), supports_seed=True, max_outputs=4
        ),
        # Price: unverified third-party figure.
        unit_price=0.04,
        price_unit="image",
    ),
    dict(
        endpoint_id="fal-ai/flux-pro/kontext",
        kind="image_to_image",
        label="FLUX.1 Kontext [pro]",
        note="Precise edits to one image.",
        sort_order=30,
        # FluxKontextInput: `image_url` (single, required), `aspect_ratio` 21:9…9:21 with
        # no auto, `num_images`, `seed`. The edit endpoint gecko-notes seeded as text →
        # image, where it 422'd every call; here its kind is right.
        image_field="image_url",
        image_field_is_list=False,
        max_images=1,
        options=_options(
            aspect_ratios=("21:9", "16:9", "4:3", "3:2", "1:1", "2:3", "3:4", "9:16", "9:21"),
            supports_seed=True,
            max_outputs=4,
        ),
        unit_price=0.04,
        price_unit="image",
    ),
    # ─── image → video ───────────────────────────────────────────────────────
    dict(
        endpoint_id="fal-ai/kling-video/v2.5-turbo/pro/image-to-video",
        kind="image_to_video",
        label="Kling 2.5 Turbo Pro",
        note="Start and end frame. 5 or 10 seconds.",
        sort_order=10,
        # KlingVideoV25TurboProImageToVideoInput: `image_url`, `tail_image_url` (the pro
        # variant only), `duration` "5"|"10" as strings, `negative_prompt`, `cfg_scale`.
        # No `seed`, no `aspect_ratio`, no `resolution`, no audio.
        image_field="image_url",
        max_images=1,
        end_image_field="tail_image_url",
        options=_options(durations=("5", "10"), supports_negative_prompt=True),
        # Price: third-party, $0.35 per five seconds.
        unit_price=0.07,
        price_unit="second",
    ),
    dict(
        endpoint_id="fal-ai/minimax/hailuo-02/standard/image-to-video",
        kind="image_to_video",
        label="MiniMax Hailuo 02 Standard",
        note="Start and end frame. The cheapest video here.",
        sort_order=20,
        # StandardImageToVideoHailuo02Input: `image_url`, `end_image_url`, `duration`
        # "6"|"10", `resolution` "512P"|"768P" (uppercase P), `prompt_optimizer`. No
        # `seed`, no `negative_prompt`, no `aspect_ratio`.
        image_field="image_url",
        max_images=1,
        end_image_field="end_image_url",
        options=_options(durations=("6", "10"), resolutions=("512P", "768P")),
        # Price: third-party, at 768P.
        unit_price=0.045,
        price_unit="second",
    ),
    dict(
        endpoint_id="fal-ai/veo3.1/fast/image-to-video",
        kind="image_to_video",
        label="Veo 3.1 Fast",
        note="Google's model. Audio is off unless asked for — it costs half as much again.",
        sort_order=30,
        # Veo31ImageToVideoInput: `image_url` and no end frame (that is the separate
        # first-last-frame endpoint), `duration` "4s"|"6s"|"8s" with the suffix,
        # `resolution` "720p"|"1080p"|"4k", `aspect_ratio` "auto"|"16:9"|"9:16",
        # `generate_audio` (default true), `negative_prompt`, `seed`.
        image_field="image_url",
        max_images=1,
        options=_options(
            aspect_ratios=("auto", "16:9", "9:16"),
            durations=("4s", "6s", "8s"),
            resolutions=("720p", "1080p", "4k"),
            supports_seed=True,
            supports_negative_prompt=True,
            supports_audio=True,
        ),
        extra_params=json.dumps({"generate_audio": False}),
        # Price: fal's Veo 3.1 Fast page, without audio, at 720p/1080p.
        unit_price=0.10,
        price_unit="second",
    ),
]


def upgrade() -> None:
    with op.batch_alter_table('asset', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ai_model', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('ai_generation_type', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('ai_prompt', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('ai_parameters', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('ai_source_assets', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('ai_seed', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('ai_generated_at', sa.DateTime(), nullable=True))

    with op.batch_alter_table('usageevent', schema=None) as batch_op:
        batch_op.add_column(sa.Column('external_ref', sqlmodel.sql.sqltypes.AutoString(), nullable=True))

    generationmodel = op.create_table('generationmodel',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('endpoint_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('label', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('note', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('image_field', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('image_field_is_list', sa.Boolean(), nullable=False),
    sa.Column('max_images', sa.Integer(), nullable=False),
    sa.Column('end_image_field', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('options', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('extra_params', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('unit_price', sa.Float(), nullable=True),
    sa.Column('price_unit', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('price_currency', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('generationmodel', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_generationmodel_endpoint_id'), ['endpoint_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_generationmodel_kind'), ['kind'], unique=False)

    # Naive UTC, the convention every timestamp column here follows (see app/clock.py),
    # spelled out rather than imported for the reason the module docstring gives.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    op.bulk_insert(
        generationmodel,
        [
            {
                "id": str(uuid.uuid4()),
                "note": "",
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
            }
            for seed in SEEDS
        ],
    )


def downgrade() -> None:
    with op.batch_alter_table('generationmodel', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_generationmodel_kind'))
        batch_op.drop_index(batch_op.f('ix_generationmodel_endpoint_id'))

    op.drop_table('generationmodel')

    with op.batch_alter_table('usageevent', schema=None) as batch_op:
        batch_op.drop_column('external_ref')

    # Dropping columns recreates `asset`, and SQLite checks foreign keys on that DROP
    # TABLE — see `7d4b9c1a6f28`, whose upgrade hit exactly this on a populated library.
    # Unlike there, the pragma has to be issued outside a transaction: SQLite ignores it
    # inside one, and the `usageevent` rebuild above has already opened one by copying
    # its rows. Without the autocommit block this downgrade failed on any library with a
    # clip, a tag or a suggestion in it.
    with op.get_context().autocommit_block():
        op.execute('PRAGMA foreign_keys=OFF')
    with op.batch_alter_table('asset', schema=None) as batch_op:
        batch_op.drop_column('ai_generated_at')
        batch_op.drop_column('ai_seed')
        batch_op.drop_column('ai_source_assets')
        batch_op.drop_column('ai_parameters')
        batch_op.drop_column('ai_prompt')
        batch_op.drop_column('ai_generation_type')
        batch_op.drop_column('ai_model')
    with op.get_context().autocommit_block():
        op.execute('PRAGMA foreign_keys=ON')
