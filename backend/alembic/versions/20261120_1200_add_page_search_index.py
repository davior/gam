"""add the page search index

A third FTS5 table, over what is written inside documents. `DocumentPage` rows have
existed since extract_text was built, and were in no index, so a phrase on page 74 of a
PDF could not be found by anything.

Filled from the pages already extracted, in the same migration: an index that starts
empty would make every document read before this upgrade unsearchable until someone
thought to extract it again, and nothing would say so.

The DDL is a literal copy of `app.search.fts.PAGE_FTS_DDL`, for the reason the first
search migration gives; test_migrations.py asserts the two agree.

Revision ID: 9a3e5d7c1b24
Revises: 5f2b8c1d9e73
Create Date: 2026-11-20 12:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = "9a3e5d7c1b24"
down_revision: Union[str, None] = "5f2b8c1d9e73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIRTUAL TABLE page_fts USING fts5(
            page_id UNINDEXED,
            asset_id UNINDEXED,
            user_id UNINDEXED,
            body,
            tokenize='porter unicode61'
        )
        """
    )
    op.execute(
        """
        INSERT INTO page_fts (page_id, asset_id, user_id, body)
        SELECT id, asset_id, user_id, text FROM documentpage WHERE trim(text) != ''
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS page_fts")
