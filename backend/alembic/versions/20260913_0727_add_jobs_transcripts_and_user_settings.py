"""add jobs, transcripts and user settings

Everything M4 needs: the background-job table, timestamped transcript segments, the
per-user settings table that holds the encrypted Deepgram key, and the transcript
header columns on asset.

Revision ID: 6e64ed25c38a
Revises: 228db8982979
Create Date: 2026-09-13 07:27:37.112100+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '6e64ed25c38a'
down_revision: Union[str, None] = '228db8982979'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('enrichmentjob',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('asset_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('stage', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('progress', sa.Integer(), nullable=False),
    sa.Column('detail', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('asset_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('model', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('error_message', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('enrichmentjob', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_enrichmentjob_asset_id'), ['asset_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_enrichmentjob_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_enrichmentjob_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_enrichmentjob_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_enrichmentjob_user_id'), ['user_id'], unique=False)
        batch_op.create_index('ix_enrichmentjob_user_status', ['user_id', 'status'], unique=False)

    op.create_table('transcriptsegment',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('asset_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('idx', sa.Integer(), nullable=False),
    sa.Column('text', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('start_time', sa.Float(), nullable=False),
    sa.Column('end_time', sa.Float(), nullable=False),
    sa.Column('speaker', sa.Integer(), nullable=True),
    sa.Column('words', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('edited', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('transcriptsegment', schema=None) as batch_op:
        batch_op.create_index('ix_segment_asset_idx', ['asset_id', 'idx'], unique=False)
        batch_op.create_index(batch_op.f('ix_transcriptsegment_asset_id'), ['asset_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_transcriptsegment_user_id'), ['user_id'], unique=False)

    op.create_table('usersetting',
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('value', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.PrimaryKeyConstraint('user_id', 'key')
    )
    with op.batch_alter_table('asset', schema=None) as batch_op:
        batch_op.add_column(sa.Column('transcript_status', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('transcript_model', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('transcript_language', sqlmodel.sql.sqltypes.AutoString(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('asset', schema=None) as batch_op:
        batch_op.drop_column('transcript_language')
        batch_op.drop_column('transcript_model')
        batch_op.drop_column('transcript_status')

    op.drop_table('usersetting')
    with op.batch_alter_table('transcriptsegment', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_transcriptsegment_user_id'))
        batch_op.drop_index(batch_op.f('ix_transcriptsegment_asset_id'))
        batch_op.drop_index('ix_segment_asset_idx')

    op.drop_table('transcriptsegment')
    with op.batch_alter_table('enrichmentjob', schema=None) as batch_op:
        batch_op.drop_index('ix_enrichmentjob_user_status')
        batch_op.drop_index(batch_op.f('ix_enrichmentjob_user_id'))
        batch_op.drop_index(batch_op.f('ix_enrichmentjob_status'))
        batch_op.drop_index(batch_op.f('ix_enrichmentjob_kind'))
        batch_op.drop_index(batch_op.f('ix_enrichmentjob_created_at'))
        batch_op.drop_index(batch_op.f('ix_enrichmentjob_asset_id'))

    op.drop_table('enrichmentjob')
