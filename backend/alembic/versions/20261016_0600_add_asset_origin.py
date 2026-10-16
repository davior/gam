"""add asset origin: the GVC project and render a gvc_export came from

GAM-3 (gvc's docs/plan-of-attack.md). Two nullable columns, set only on uploads that
Video Creator files back into the library with `source = "gvc_export"`:

- `origin_project_id`: the GVC project. Indexed, because "this project's renders" is
  the one question asked of it.
- `origin_reference_id`: the GVC render job that made the file.

No foreign keys: both are ids in another application's database.

Adding columns and an index is plain ALTER on SQLite, so the upgrade does not recreate
`asset`. The downgrade's drops do, hence the PRAGMA dance `a8e1c0f4d2b6` explains.

Revision ID: 5b9e3d71c2a4
Revises: a8e1c0f4d2b6
Create Date: 2026-10-16 06:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '5b9e3d71c2a4'
down_revision: Union[str, None] = 'a8e1c0f4d2b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('asset', schema=None) as batch_op:
        batch_op.add_column(sa.Column('origin_project_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('origin_reference_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.create_index('ix_asset_origin_project_id', ['origin_project_id'], unique=False)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute('PRAGMA foreign_keys=OFF')
    with op.batch_alter_table('asset', schema=None) as batch_op:
        batch_op.drop_index('ix_asset_origin_project_id')
        batch_op.drop_column('origin_reference_id')
        batch_op.drop_column('origin_project_id')
    with op.get_context().autocommit_block():
        op.execute('PRAGMA foreign_keys=ON')
