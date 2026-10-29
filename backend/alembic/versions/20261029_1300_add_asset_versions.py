"""add assetversion: standard-resolution files of a video

A video's 1080p, 720p and so on, beside its original (app/models/version.py). Rows of
their own rather than Asset rows, so a version shares its asset's name, tags,
attribution and transcript instead of carrying a copy. A new table only: nothing
existing changes, and the downgrade drops it whole.

Revision ID: aecf97cd87b1
Revises: f3a7d2c18e54
Create Date: 2026-10-29 04:34:10.980967+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'aecf97cd87b1'
down_revision: Union[str, None] = 'f3a7d2c18e54'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('assetversion',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('asset_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('short_side', sa.Integer(), nullable=False),
    sa.Column('storage_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('mime_type', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('checksum_sha256', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('codec', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['asset.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('asset_id', 'short_side', name='ux_assetversion_size')
    )
    with op.batch_alter_table('assetversion', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_assetversion_asset_id'), ['asset_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_assetversion_user_id'), ['user_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('assetversion', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_assetversion_user_id'))
        batch_op.drop_index(batch_op.f('ix_assetversion_asset_id'))

    op.drop_table('assetversion')
