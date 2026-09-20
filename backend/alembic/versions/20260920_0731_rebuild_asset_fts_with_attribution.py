"""rebuild asset_fts with an attribution column

M10. Finding an asset by its publisher is half the point of recording one, so the
attribution fields have to reach the keyword index.

**SQLite FTS5 has no ALTER TABLE ADD COLUMN.** The only way to add `attribution_text` is
to drop the virtual table and create it again — and because `asset_fts` stores its own
copy of the text rather than using external-content mode (see the reasoning at the top of
app/search/fts.py), dropping it destroys the index for every existing asset. So this
migration repopulates, and that repopulate is not optional decoration: a recreate without
it passes any test that only compares columns, and leaves a populated library with
keyword search silently returning nothing until each asset happens to be edited again.

Split from `3f7a21c9d4e5` precisely because this half can fail and that half cannot. If
this revision dies partway, the columns are still committed and this is re-runnable;
folded together, a failure here would strand them mid-migration.

The repopulated text is composed to match `app/search/fts.py::index_asset`, so the first
ordinary edit of an asset does not silently rewrite its index entry into something
different. Tag order differs from `tags_text_for`'s (group_concat does not promise one)
and that is immaterial — FTS5 tokenises, so order never reaches the index.

`published_date` and `retrieved_at` are deliberately left out of the text: both are
served exactly by the date-range filter on `/api/assets`, and a bare year in a free-text
index mostly collides with titles rather than helping.

Revision ID: 9c2e08b4a1f7
Revises: 3f7a21c9d4e5
Create Date: 2026-09-20 07:31:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = '9c2e08b4a1f7'
down_revision: Union[str, None] = '3f7a21c9d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# This migration carries its own literal copy of the DDL rather than importing the
# constants in app/search/fts.py, per the convention that migration set out: a migration
# has to describe the schema as it was when it was written, and one that imports live
# code silently changes meaning when that code changes. test_migrations.py asserts the
# two copies still agree, which is what stops them drifting unnoticed.
ASSET_FTS_DDL = """
CREATE VIRTUAL TABLE asset_fts USING fts5(
    asset_id UNINDEXED,
    user_id UNINDEXED,
    name,
    description,
    summary,
    tags_text,
    attribution_text,
    tokenize='porter unicode61'
)
"""

REPOPULATE = """
INSERT INTO asset_fts (
    asset_id, user_id, name, description, summary, tags_text, attribution_text
)
SELECT
    a.id,
    a.user_id,
    COALESCE(a.name, ''),
    COALESCE(a.description, ''),
    COALESCE(a.summary, ''),
    COALESCE(
        (SELECT group_concat(t.name, ' ')
           FROM assettag at
           JOIN tag t ON t.id = at.tag_id
          WHERE at.asset_id = a.id),
        ''
    ),
    TRIM(
        COALESCE(a.creator, '') || ' ' ||
        COALESCE(a.publisher, '') || ' ' ||
        COALESCE(a.source_title, '') || ' ' ||
        COALESCE(a.license, '') || ' ' ||
        COALESCE(a.credit_line, '') || ' ' ||
        COALESCE(a.source_url, '')
    )
FROM asset a
"""

# The pre-M10 shape, for downgrade. Same reasoning: literal, not imported.
ASSET_FTS_DDL_WITHOUT_ATTRIBUTION = """
CREATE VIRTUAL TABLE asset_fts USING fts5(
    asset_id UNINDEXED,
    user_id UNINDEXED,
    name,
    description,
    summary,
    tags_text,
    tokenize='porter unicode61'
)
"""

REPOPULATE_WITHOUT_ATTRIBUTION = """
INSERT INTO asset_fts (asset_id, user_id, name, description, summary, tags_text)
SELECT
    a.id,
    a.user_id,
    COALESCE(a.name, ''),
    COALESCE(a.description, ''),
    COALESCE(a.summary, ''),
    COALESCE(
        (SELECT group_concat(t.name, ' ')
           FROM assettag at
           JOIN tag t ON t.id = at.tag_id
          WHERE at.asset_id = a.id),
        ''
    )
FROM asset a
"""


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS asset_fts")
    op.execute(ASSET_FTS_DDL)
    op.execute(REPOPULATE)


def downgrade() -> None:
    # Symmetric, and repopulating for the same reason: a downgrade that left the index
    # empty would be a silent data-shaped loss, not a schema change.
    op.execute("DROP TABLE IF EXISTS asset_fts")
    op.execute(ASSET_FTS_DDL_WITHOUT_ATTRIBUTION)
    op.execute(REPOPULATE_WITHOUT_ATTRIBUTION)
