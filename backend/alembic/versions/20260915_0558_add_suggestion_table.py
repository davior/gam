"""add suggestion table

What an enrichment run proposed, pending a person saying yes (FR 9.1.4). Deliberately
its own table rather than a status column on `assettag`: a suggested tag that lived in
the join would have to be excluded by every existing tag query, and the one that forgot
would put an unapproved tag into the search index.

Revision ID: bb179c9bcf3d
Revises: 247c2a57f00e
Create Date: 2026-09-15 05:58:42.505733+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'bb179c9bcf3d'
down_revision: Union[str, None] = '247c2a57f00e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('suggestion',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('asset_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('value', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('model', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['asset_id'], ['asset.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('suggestion', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_suggestion_asset_id'), ['asset_id'], unique=False)
        batch_op.create_index('ix_suggestion_asset_status', ['asset_id', 'status'], unique=False)
        batch_op.create_index(batch_op.f('ix_suggestion_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_suggestion_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('suggestion', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_suggestion_user_id'))
        batch_op.drop_index(batch_op.f('ix_suggestion_status'))
        batch_op.drop_index('ix_suggestion_asset_status')
        batch_op.drop_index(batch_op.f('ix_suggestion_asset_id'))

    op.drop_table('suggestion')
