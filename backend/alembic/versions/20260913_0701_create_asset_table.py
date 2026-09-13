"""create asset table

The library's catalogue. One table for everything the library holds — clips and AI
generations (later milestones) are Assets too, so that listing, filtering and searching
never become a union across tables.

Revision ID: 228db8982979
Revises: 8eb962365b3d
Create Date: 2026-09-13 07:01:35.142965+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '228db8982979'
down_revision: Union[str, None] = '8eb962365b3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('asset',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('summary', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('asset_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('source', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('storage_key', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('thumb_key', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('original_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('mime_type', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('file_format', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('checksum_sha256', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('duration_seconds', sa.Float(), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('codec', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('field_provenance', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('upload_date', sa.DateTime(), nullable=False),
    sa.Column('modified_date', sa.DateTime(), nullable=False),
    sa.Column('metadata_modified_date', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('asset', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_asset_asset_type'), ['asset_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_asset_checksum_sha256'), ['checksum_sha256'], unique=False)
        batch_op.create_index(batch_op.f('ix_asset_upload_date'), ['upload_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_asset_user_id'), ['user_id'], unique=False)
        batch_op.create_index('ix_asset_user_upload', ['user_id', 'upload_date'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('asset', schema=None) as batch_op:
        batch_op.drop_index('ix_asset_user_upload')
        batch_op.drop_index(batch_op.f('ix_asset_user_id'))
        batch_op.drop_index(batch_op.f('ix_asset_upload_date'))
        batch_op.drop_index(batch_op.f('ix_asset_checksum_sha256'))
        batch_op.drop_index(batch_op.f('ix_asset_asset_type'))

    op.drop_table('asset')
