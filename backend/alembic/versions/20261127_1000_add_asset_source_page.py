"""add asset.source_page

The page of a PDF a rendered picture came from, so "which page was this" is a column and
not a convention in a name. Nullable and unused by every existing row.

Revision ID: b4c7e19a2d58
Revises: 9a3e5d7c1b24
Create Date: 2026-11-27 10:00:00.000000+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4c7e19a2d58"
down_revision: Union[str, None] = "9a3e5d7c1b24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.add_column(sa.Column("source_page", sa.Integer(), nullable=True))


def downgrade() -> None:
    # The drop recreates `asset` on SQLite; see `a8e1c0f4d2b6` for why foreign keys are
    # switched off around it (outside a transaction, where the pragma is honoured).
    with op.get_context().autocommit_block():
        op.execute("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table("asset") as batch:
        batch.drop_column("source_page")
    with op.get_context().autocommit_block():
        op.execute("PRAGMA foreign_keys=ON")
