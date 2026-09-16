"""add documentpage table

Readable text pulled out of a document, so `summarize` and `autotag` have something to
read for a PDF or a Word file. Until now they refused: `enrichment/source.py` raised
`NoSourceMaterial` saying so out loud, because the `extract_text` job the plan lists had
never been built.

One row per page, slide, sheet or chunk rather than a column on `asset`, because
SQLModel selects every column of every row and the library listing selects assets by the
page — a 200-page PDF's text in an asset column would be read from disk to render a
thumbnail grid that never looks at it. No foreign key to `asset`, matching
`transcriptsegment`; the delete cascade is explicit in `services/assets.py`.

Revision ID: 56ac14e89a0c
Revises: c4954d1715e7
Create Date: 2026-09-16 11:11:08.578568+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '56ac14e89a0c'
down_revision: Union[str, None] = 'c4954d1715e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('documentpage',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('asset_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('idx', sa.Integer(), nullable=False),
    sa.Column('page_number', sa.Integer(), nullable=True),
    sa.Column('label', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('text', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('documentpage', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_documentpage_asset_id'), ['asset_id'], unique=False)
        batch_op.create_index('ix_documentpage_asset_idx', ['asset_id', 'idx'], unique=False)
        batch_op.create_index(batch_op.f('ix_documentpage_user_id'), ['user_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('documentpage', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_documentpage_user_id'))
        batch_op.drop_index('ix_documentpage_asset_idx')
        batch_op.drop_index(batch_op.f('ix_documentpage_asset_id'))

    op.drop_table('documentpage')
