"""add audio_kind to asset

What an audio file is: music, speech or a sound effect, NULL when not known. It decides
whether an upload is transcribed (music and sound effects are not) and lets Video
Creator keep voice recordings out of a soundtrack search. See app/music.py.

Existing rows stay NULL, which is treated exactly as before this column existed:
transcribed and analysed. The "Read file tags" library run fills it from tags.

Revision ID: e4b2c9d17a35
Revises: d71e4b9a2c86
Create Date: 2026-10-26 12:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'e4b2c9d17a35'
down_revision: Union[str, None] = 'd71e4b9a2c86'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('asset', schema=None) as batch_op:
        batch_op.add_column(sa.Column('audio_kind', sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    # The drop recreates `asset` on SQLite; see `a8e1c0f4d2b6` for why foreign keys are
    # switched off around it.
    with op.get_context().autocommit_block():
        op.execute('PRAGMA foreign_keys=OFF')
    with op.batch_alter_table('asset', schema=None) as batch_op:
        batch_op.drop_column('audio_kind')
    with op.get_context().autocommit_block():
        op.execute('PRAGMA foreign_keys=ON')
