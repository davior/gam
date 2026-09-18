"""add clip columns to asset

M7's clip and sub-video extraction both need a parent pointer and a time range.
`parent_asset_id` carries a real foreign key, unlike `transcriptsegment`/
`documentpage`'s no-FK-plus-explicit-cleanup pattern: a live clip (`storage_key IS
NULL`) is meant to *block* its parent's deletion until it is promoted or removed, and
SQLite enforcing that is the safety net behind `services/assets.py::delete_asset`'s own
guard — the same belt-and-suspenders role the FK already plays for `assettag` and
`suggestion`. No prior migration adds a foreign key to a table that already exists (both
of those got theirs at `create_table` time); `create_foreign_key` inside batch mode is
what SQLite's "recreate the table" batch strategy needs to add one after the fact.

Revision ID: 7d4b9c1a6f28
Revises: 56ac14e89a0c
Create Date: 2026-09-18 09:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '7d4b9c1a6f28'
down_revision: Union[str, None] = '56ac14e89a0c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('asset', schema=None) as batch_op:
        batch_op.add_column(sa.Column('parent_asset_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('in_point', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('out_point', sa.Float(), nullable=True))
        batch_op.create_index('ix_asset_parent_asset_id', ['parent_asset_id'], unique=False)
        batch_op.create_foreign_key('fk_asset_parent_asset_id_asset', 'asset', ['parent_asset_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('asset', schema=None) as batch_op:
        batch_op.drop_constraint('fk_asset_parent_asset_id_asset', type_='foreignkey')
        batch_op.drop_index('ix_asset_parent_asset_id')
        batch_op.drop_column('out_point')
        batch_op.drop_column('in_point')
        batch_op.drop_column('parent_asset_id')
