"""add embedding table

Vectors as raw float32 bytes. A 512-dimension vector is 2 KB packed against roughly
10 KB as JSON text, and packed bytes go straight into numpy with no decode step — which
is what lets brute-force cosine over a whole library stay fast enough to not need a
vector database.

Revision ID: 584b2e6c9e00
Revises: c4f2a1b09e77
Create Date: 2026-09-13 12:13:25.069628+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '584b2e6c9e00'
down_revision: Union[str, None] = 'c4f2a1b09e77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('embedding',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('owner_kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('owner_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('asset_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('model', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('dim', sa.Integer(), nullable=False),
    sa.Column('vector', sa.LargeBinary(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('embedding', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_embedding_asset_id'), ['asset_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_embedding_owner_id'), ['owner_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_embedding_owner_kind'), ['owner_kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_embedding_user_id'), ['user_id'], unique=False)
        batch_op.create_index('ix_embedding_user_model', ['user_id', 'model'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('embedding', schema=None) as batch_op:
        batch_op.drop_index('ix_embedding_user_model')
        batch_op.drop_index(batch_op.f('ix_embedding_user_id'))
        batch_op.drop_index(batch_op.f('ix_embedding_owner_kind'))
        batch_op.drop_index(batch_op.f('ix_embedding_owner_id'))
        batch_op.drop_index(batch_op.f('ix_embedding_asset_id'))

    op.drop_table('embedding')
