"""add FTS5 search indexes

Two virtual tables: one over an asset's own metadata, one over what is said inside it.
Written as raw SQL because SQLModel has no notion of a virtual table.

That invisibility cuts the other way, and dangerously: autogenerate sees tables present
in the database but absent from the models, and writes DROP statements for them. The
first run after this migration produced eleven, which between them would have destroyed
the entire search index. `include_object` in alembic/env.py is what stops that, and it
has to stay.

The UNINDEXED columns are along for the ride: FTS5 stores them so a hit can be resolved
to an asset and a timestamp without a second query, but does not tokenise them, so an
asset id cannot pollute a text match.

Revision ID: c4f2a1b09e77
Revises: 6e64ed25c38a
Create Date: 2026-09-13 12:10:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c4f2a1b09e77"
down_revision: Union[str, None] = "6e64ed25c38a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # porter unicode61: stemming means "deploying" matches a search for "deploy", which
    # is what someone half-remembering a phrase actually types. unicode61 folds accents
    # and case.
    op.execute(
        """
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
    )
    op.execute(
        """
        CREATE VIRTUAL TABLE segment_fts USING fts5(
            segment_id UNINDEXED,
            asset_id UNINDEXED,
            user_id UNINDEXED,
            body,
            start_time UNINDEXED,
            tokenize='porter unicode61'
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS segment_fts")
    op.execute("DROP TABLE IF EXISTS asset_fts")
