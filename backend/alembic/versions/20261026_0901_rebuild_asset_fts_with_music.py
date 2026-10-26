"""rebuild asset_fts with a music column

A music profile nobody can search for is no use to the one thing that wants it: Video
Creator looking for "tense slow piano". So it reaches the keyword index as `music_text`.

FTS5 has no ALTER TABLE ADD COLUMN, so this drops and recreates the table and then
repopulates it, exactly as `9c2e08b4a1f7` did for attribution; that migration's
docstring explains why the repopulate is not optional. The new column is appended last
because `search_assets` addresses `name` by column index in snippet().

The repopulated text is composed to match `app/search/fts.py::index_asset` and
`app/music.py::index_text`, so the first ordinary edit of an asset does not silently
rewrite its index entry into something different. The music columns were only added by
the revision before, so on a real upgrade they are all NULL and `music_text` comes out
empty. It is composed properly anyway, so a downgrade and re-upgrade of a library that
has profiles does not lose them from the index.

Revision ID: d71e4b9a2c86
Revises: c3a8f15e7b20
Create Date: 2026-10-26 09:01:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd71e4b9a2c86'
down_revision: Union[str, None] = 'c3a8f15e7b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Literal copies, not imports from app/search/fts.py: a migration has to describe the
# schema as it was when it was written. test_migrations.py asserts they still agree.
ASSET_FTS_DDL = """
CREATE VIRTUAL TABLE asset_fts USING fts5(
    asset_id UNINDEXED,
    user_id UNINDEXED,
    name,
    description,
    summary,
    tags_text,
    attribution_text,
    music_text,
    tokenize='porter unicode61'
)
"""

_TAGS_TEXT = """
    COALESCE(
        (SELECT group_concat(t.name, ' ')
           FROM assettag at
           JOIN tag t ON t.id = at.tag_id
          WHERE at.asset_id = a.id),
        ''
    )"""

_ATTRIBUTION_TEXT = """
    TRIM(
        COALESCE(a.creator, '') || ' ' ||
        COALESCE(a.publisher, '') || ' ' ||
        COALESCE(a.source_title, '') || ' ' ||
        COALESCE(a.license, '') || ' ' ||
        COALESCE(a.credit_line, '') || ' ' ||
        COALESCE(a.source_url, '')
    )"""

# json_each over a NULL or non-array yields no rows, so an unprofiled asset gets ''.
# json_valid guards a malformed value, which `music.decode_terms` also reads as empty.
_MUSIC_TEXT = """
    TRIM(
        COALESCE((SELECT group_concat(j.value, ' ') FROM json_each(
            CASE WHEN json_valid(a.music_genres) THEN a.music_genres END) j), '') || ' ' ||
        COALESCE((SELECT group_concat(j.value, ' ') FROM json_each(
            CASE WHEN json_valid(a.music_moods) THEN a.music_moods END) j), '') || ' ' ||
        COALESCE((SELECT group_concat(j.value, ' ') FROM json_each(
            CASE WHEN json_valid(a.music_instruments) THEN a.music_instruments END) j), '') || ' ' ||
        CASE a.music_vocals WHEN 1 THEN 'vocals' WHEN 0 THEN 'instrumental' ELSE '' END || ' ' ||
        CASE WHEN a.music_energy IN ('low', 'medium', 'high')
             THEN a.music_energy || ' energy' ELSE '' END
    )"""

REPOPULATE = f"""
INSERT INTO asset_fts (
    asset_id, user_id, name, description, summary, tags_text, attribution_text, music_text
)
SELECT
    a.id,
    a.user_id,
    COALESCE(a.name, ''),
    COALESCE(a.description, ''),
    COALESCE(a.summary, ''),
    {_TAGS_TEXT},
    {_ATTRIBUTION_TEXT},
    {_MUSIC_TEXT}
FROM asset a
"""

# The previous shape, for downgrade. Same reasoning: literal, not imported.
ASSET_FTS_DDL_WITHOUT_MUSIC = """
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

REPOPULATE_WITHOUT_MUSIC = f"""
INSERT INTO asset_fts (
    asset_id, user_id, name, description, summary, tags_text, attribution_text
)
SELECT
    a.id,
    a.user_id,
    COALESCE(a.name, ''),
    COALESCE(a.description, ''),
    COALESCE(a.summary, ''),
    {_TAGS_TEXT},
    {_ATTRIBUTION_TEXT}
FROM asset a
"""


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS asset_fts")
    op.execute(ASSET_FTS_DDL)
    op.execute(REPOPULATE)


def downgrade() -> None:
    # Repopulating for the same reason as the upgrade: an emptied index is a silent,
    # data-shaped loss.
    op.execute("DROP TABLE IF EXISTS asset_fts")
    op.execute(ASSET_FTS_DDL_WITHOUT_MUSIC)
    op.execute(REPOPULATE_WITHOUT_MUSIC)
