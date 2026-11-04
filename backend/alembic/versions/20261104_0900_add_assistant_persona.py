"""add the persona an assistant turn was asked as

A turn may be asked as one of the suite's personas. Kept on the turn so a rerun after a
restart is asked as the same one. Nullable: every existing turn was asked as none.

Revision ID: e8c41a7b2d56
Revises: c3a95e7d1f42
Create Date: 2026-11-04 09:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'e8c41a7b2d56'
down_revision: Union[str, None] = 'c3a95e7d1f42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('assistant_turn', schema=None) as batch_op:
        batch_op.add_column(sa.Column('persona_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('assistant_turn', schema=None) as batch_op:
        batch_op.drop_column('persona_id')
