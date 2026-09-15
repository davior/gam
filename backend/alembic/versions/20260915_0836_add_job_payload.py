"""add job payload

Somewhere for a job to carry what does not fit a column. Only bulk enrichment uses it:
which action to run, and over which assets. Nullable, so every existing row is valid
without a backfill.

Revision ID: c4954d1715e7
Revises: 6e16fbcffdce
Create Date: 2026-09-15 08:36:56.559949+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'c4954d1715e7'
down_revision: Union[str, None] = '6e16fbcffdce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('enrichmentjob', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payload', sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('enrichmentjob', schema=None) as batch_op:
        batch_op.drop_column('payload')
