"""let the seeded FLUX rows take a custom width × height

FLUX's `image_size` is a named preset *or* `{width, height}`, and the presets stop at
1024 on the long side: half the width of a 1080p frame. `options.custom_size` lets a row
say it takes any size within limits, so Video Creator can ask for a picture big enough
for the video it is going into.

The limits are deliberately below what fal's schema allows. FLUX.1 was trained up to
about two megapixels, and well past that it starts repeating subjects across the
picture; five million pixels reaches 1.5× a 1080p frame (2880×1620) without going far
beyond. An admin can widen or narrow them per row.

A row whose options already carry `custom_size` (an admin set it) is left alone, and
the downgrade removes the key only where it still holds what this revision wrote.

Revision ID: f3a7d2c18e54
Revises: e4b2c9d17a35
Create Date: 2026-10-29 12:00:00.000000+00:00
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a7d2c18e54'
down_revision: Union[str, None] = 'e4b2c9d17a35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ENDPOINTS = ("fal-ai/flux/schnell", "fal-ai/flux/dev")
CUSTOM_SIZE = {"max_width": 3072, "max_height": 3072, "multiple_of": 16, "max_pixels": 5_000_000}


def _rows(conn):
    return conn.execute(
        sa.text("SELECT id, options FROM generationmodel WHERE endpoint_id IN :endpoints")
        .bindparams(sa.bindparam("endpoints", expanding=True)),
        {"endpoints": list(ENDPOINTS)},
    ).fetchall()


def _decoded(text):
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _write(conn, row_id, options):
    conn.execute(
        sa.text("UPDATE generationmodel SET options = :options WHERE id = :id"),
        {"options": json.dumps(options), "id": row_id},
    )


def upgrade() -> None:
    conn = op.get_bind()
    for row_id, text in _rows(conn):
        options = _decoded(text)
        if options is None or options.get("custom_size") is not None:
            continue
        _write(conn, row_id, {**options, "custom_size": CUSTOM_SIZE})


def downgrade() -> None:
    conn = op.get_bind()
    for row_id, text in _rows(conn):
        options = _decoded(text)
        if options is None or options.get("custom_size") != CUSTOM_SIZE:
            continue
        options.pop("custom_size")
        _write(conn, row_id, options)
