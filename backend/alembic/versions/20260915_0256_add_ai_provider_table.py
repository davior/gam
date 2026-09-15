"""add ai provider table

A row per configured generative LLM, rather than more keys in `usersetting`: a user
keeps several at once — a local Ollama for cheap work, Claude for anything needing
vision — and `is_active` picks between them. The credential is Fernet-encrypted by the
application before it reaches this column; nothing here decrypts it.

Revision ID: 247c2a57f00e
Revises: 997f000122a7
Create Date: 2026-09-15 02:56:03.042297+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '247c2a57f00e'
down_revision: Union[str, None] = '997f000122a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('aiprovider',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('provider_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('api_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('base_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('model', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('max_tokens', sa.Integer(), nullable=False),
    sa.Column('supports_images', sa.Boolean(), nullable=False),
    sa.Column('use_anthropic_api', sa.Boolean(), nullable=False),
    sa.Column('extra_params', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('aiprovider', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_aiprovider_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('aiprovider', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_aiprovider_user_id'))

    op.drop_table('aiprovider')
