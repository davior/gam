"""add the music profile to asset

What a track sounds like (genres, moods, instruments, tempo, key, vocals, energy), so
Video Creator can find a soundtrack that fits a scene. See app/music.py.

All nullable and all plain ALTER on SQLite, so the upgrade does not recreate `asset`.
The downgrade's drops do, hence the PRAGMA dance `a8e1c0f4d2b6` explains.

Split from `d71e4b9a2c86`, which rebuilds the search index, for the reason `3f7a21c9d4e5`
and `9c2e08b4a1f7` were split: that half can fail and this half cannot.

Revision ID: c3a8f15e7b20
Revises: 5b9e3d71c2a4
Create Date: 2026-10-26 09:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'c3a8f15e7b20'
down_revision: Union[str, None] = '5b9e3d71c2a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('asset', schema=None) as batch_op:
        batch_op.add_column(sa.Column('music_genres', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('music_moods', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('music_instruments', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('music_bpm', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('music_key', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('music_vocals', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('music_energy', sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute('PRAGMA foreign_keys=OFF')
    with op.batch_alter_table('asset', schema=None) as batch_op:
        batch_op.drop_column('music_energy')
        batch_op.drop_column('music_vocals')
        batch_op.drop_column('music_key')
        batch_op.drop_column('music_bpm')
        batch_op.drop_column('music_instruments')
        batch_op.drop_column('music_moods')
        batch_op.drop_column('music_genres')
    with op.get_context().autocommit_block():
        op.execute('PRAGMA foreign_keys=ON')
