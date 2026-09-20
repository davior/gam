"""add attribution columns to asset

M10. Eight columns recording *whose work* an asset is, as opposed to `source`, which
records how the file arrived. The two are conflated constantly and answer different
questions; see docs/m10-attribution.md for the decisions behind the field set.

Two of the columns look wrong at a glance and are not:

- `published_date` is a string, not a DateTime. Publication dates are routinely partial
  ("1994", "March 2019") and a DateTime cannot hold either without inventing a precision
  that then reads as real. ISO 8601 partial dates compare correctly as plain strings, so
  the filters that use it need no parsing. It is indexed because the date range filter
  orders by it.
- `license` shadows a Python builtin name only in the interactive interpreter's `site`
  namespace, never as a model attribute or a SQL identifier. SQLite has no reserved word
  here and SQLAlchemy quotes identifiers regardless.

Adding columns and an index are both things SQLite's ALTER supports directly, so batch
mode does not recreate `asset` here and the PRAGMA foreign_keys dance that
`7d4b9c1a6f28` needed does not apply — nothing drops the table, so nothing can trip over
a row referencing it.

The `asset_fts` column that makes these searchable is deliberately *not* here: it has to
drop and rebuild a virtual table, which is the risky half, and it gets its own revision
so a failure there does not strand these columns.

Revision ID: 3f7a21c9d4e5
Revises: 7d4b9c1a6f28
Create Date: 2026-09-20 07:30:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '3f7a21c9d4e5'
down_revision: Union[str, None] = '7d4b9c1a6f28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('asset', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('creator', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('publisher', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('source_title', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('published_date', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('retrieved_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('license', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('credit_line', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.create_index('ix_asset_published_date', ['published_date'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('asset', schema=None) as batch_op:
        batch_op.drop_index('ix_asset_published_date')
        batch_op.drop_column('credit_line')
        batch_op.drop_column('license')
        batch_op.drop_column('retrieved_at')
        batch_op.drop_column('published_date')
        batch_op.drop_column('source_title')
        batch_op.drop_column('publisher')
        batch_op.drop_column('creator')
        batch_op.drop_column('source_url')
