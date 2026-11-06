"""add playlists

Two plain tables: a playlist, and its items (an asset plus an optional range). Nothing
existing changes, so downgrading drops them and loses only playlists.

Revision ID: 5f2b8c1d9e73
Revises: e8c41a7b2d56
Create Date: 2026-11-06 12:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '5f2b8c1d9e73'
down_revision: Union[str, None] = 'e8c41a7b2d56'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('playlist',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('playlist', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_playlist_user_id'), ['user_id'], unique=False)

    op.create_table('playlist_item',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('playlist_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('asset_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('in_point', sa.Float(), nullable=True),
    sa.Column('out_point', sa.Float(), nullable=True),
    sa.Column('image_seconds', sa.Float(), nullable=True),
    sa.Column('label', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('note', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['asset.id'], ),
    sa.ForeignKeyConstraint(['playlist_id'], ['playlist.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('playlist_item', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_playlist_item_asset_id'), ['asset_id'], unique=False)
        batch_op.create_index('ix_playlist_item_order', ['playlist_id', 'position'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('playlist_item', schema=None) as batch_op:
        batch_op.drop_index('ix_playlist_item_order')
        batch_op.drop_index(batch_op.f('ix_playlist_item_asset_id'))
    op.drop_table('playlist_item')
    with op.batch_alter_table('playlist', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_playlist_user_id'))
    op.drop_table('playlist')
