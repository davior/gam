"""add the assistant's session and turn tables

The GAM assistant (saved conversations, one background job per turn, a proposal the
user applies). Two plain tables; nothing existing changes.

Revision ID: c3a95e7d1f42
Revises: b7d41e9c2a60
Create Date: 2026-11-02 14:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'c3a95e7d1f42'
down_revision: Union[str, None] = 'b7d41e9c2a60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('assistant_session',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('messages', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('assistant_session', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_assistant_session_user_id'), ['user_id'], unique=False)

    op.create_table('assistant_turn',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('session_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('stage', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('progress', sa.Integer(), nullable=False),
    sa.Column('detail', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('error_message', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('prompt', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('focus_asset_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('steps', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('reply', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('proposal', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('outcome', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('cost', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['assistant_session.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('assistant_turn', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_assistant_turn_session_id'), ['session_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_assistant_turn_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_assistant_turn_status'), ['status'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('assistant_turn', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_assistant_turn_status'))
        batch_op.drop_index(batch_op.f('ix_assistant_turn_user_id'))
        batch_op.drop_index(batch_op.f('ix_assistant_turn_session_id'))
    op.drop_table('assistant_turn')
    with op.batch_alter_table('assistant_session', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_assistant_session_user_id'))
    op.drop_table('assistant_session')
