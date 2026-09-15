"""add usage event table

What each call to a paid service consumed, so FR 8.1.4 can answer "how much" per asset
and per library. `asset_id` is deliberately not a foreign key: deleting an asset must not
delete the record of what was spent on it.

Revision ID: 6e16fbcffdce
Revises: bb179c9bcf3d
Create Date: 2026-09-15 07:39:46.652447+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '6e16fbcffdce'
down_revision: Union[str, None] = 'bb179c9bcf3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('usageevent',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('asset_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('provider', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('model', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('units', sa.Integer(), nullable=False),
    sa.Column('unit_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('cost', sa.Float(), nullable=True),
    sa.Column('currency', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('cost_estimated', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('usageevent', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_usageevent_asset_id'), ['asset_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_usageevent_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_usageevent_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_usageevent_provider'), ['provider'], unique=False)
        batch_op.create_index('ix_usageevent_user_created', ['user_id', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_usageevent_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('usageevent', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_usageevent_user_id'))
        batch_op.drop_index('ix_usageevent_user_created')
        batch_op.drop_index(batch_op.f('ix_usageevent_provider'))
        batch_op.drop_index(batch_op.f('ix_usageevent_kind'))
        batch_op.drop_index(batch_op.f('ix_usageevent_created_at'))
        batch_op.drop_index(batch_op.f('ix_usageevent_asset_id'))

    op.drop_table('usageevent')
