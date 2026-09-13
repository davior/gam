"""Keyword search, on SQLite's FTS5.

Two indexes, because the two things being searched answer different questions. An
asset index matches what the *file* is called and how it was described; a segment index
matches what somebody *said* inside it, and carries the timestamp that makes the hit
useful.

The tables store their own copy of the text rather than using FTS5's external-content
mode. External content saves that duplication, but it needs an integer rowid to point
at — this schema uses string uuids — and it breaks `snippet()`, which is the whole
reason a search result is readable. The duplication is text measured in kilobytes
against media measured in gigabytes.

Sync is explicit: callers say when something changed. Triggers would be automatic and
would also be invisible to tests, impossible to reason about across an Alembic
migration, and would fire inside transactions this code does not control.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy import text
from sqlmodel import Session

from app.models.asset import Asset
from app.models.transcript import TranscriptSegment

logger = logging.getLogger(__name__)

ASSET_FTS = "asset_fts"
SEGMENT_FTS = "segment_fts"

# The virtual tables, as DDL.
#
# The Alembic migration that creates them carries its own literal copy rather than
# importing these: a migration has to describe the schema as it was when it was written,
# and one that imports live code silently changes meaning when that code changes. These
# exist so tests can build the same tables without running the whole chain per test.
# test_migrations.py asserts the real migration produces these same columns, which is
# what stops the two copies drifting apart unnoticed.
#
# UNINDEXED columns ride along so a hit resolves to an asset and a timestamp without a
# second query, while staying out of the tokeniser — an asset id must not match a text
# search.
ASSET_FTS_DDL = """
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

SEGMENT_FTS_DDL = """
CREATE VIRTUAL TABLE segment_fts USING fts5(
    segment_id UNINDEXED,
    asset_id UNINDEXED,
    user_id UNINDEXED,
    body,
    start_time UNINDEXED,
    tokenize='porter unicode61'
)
"""

ASSET_FTS_COLUMNS = ("asset_id", "user_id", "name", "description", "summary", "tags_text")
SEGMENT_FTS_COLUMNS = ("segment_id", "asset_id", "user_id", "body", "start_time")

# The virtual tables, plus the shadow tables SQLite creates behind each one
# (_data, _idx, _content, _docsize, _config).
FTS_TABLE_PREFIXES = (ASSET_FTS, SEGMENT_FTS)


def is_fts_table(name: str) -> bool:
    return any(name == base or name.startswith(f"{base}_") for base in FTS_TABLE_PREFIXES)


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Alembic autogenerate filter. Lives here, and is imported by alembic/env.py.

    None of these tables appear in SQLModel.metadata — SQLAlchemy has no notion of a
    virtual table — so autogenerate sees them in the database, fails to find them in the
    models, and helpfully writes DROP statements.

    That is not hypothetical: the first autogenerate run after the search index was
    added produced eleven drop_table calls which between them would have destroyed the
    whole index. This filter is what stops the next one, and test_migrations.py asserts
    it still does.
    """
    if type_ == "table" and is_fts_table(name):
        return False
    return True


def create_search_tables(connection) -> None:
    """Build the virtual tables on a connection that has none.

    Used by tests. Production gets them from the migration.
    """
    connection.execute(text(ASSET_FTS_DDL))
    connection.execute(text(SEGMENT_FTS_DDL))


@dataclass
class FtsHit:
    asset_id: str
    score: float          # bm25, lower is better — SQLite returns it negated already
    snippet: str
    segment_id: Optional[str] = None
    start_time: Optional[float] = None


# ─── writing ─────────────────────────────────────────────────────────────────


def index_asset(session: Session, asset: Asset, tags_text: str = "") -> None:
    """Re-index one asset's own metadata. Safe to call repeatedly."""
    remove_asset(session, asset.id)
    session.execute(
        text(
            f"INSERT INTO {ASSET_FTS} (asset_id, user_id, name, description, summary, tags_text)"
            " VALUES (:asset_id, :user_id, :name, :description, :summary, :tags_text)"
        ),
        {
            "asset_id": asset.id,
            "user_id": asset.user_id,
            "name": asset.name or "",
            "description": asset.description or "",
            "summary": asset.summary or "",
            "tags_text": tags_text,
        },
    )
    session.commit()


def remove_asset(session: Session, asset_id: str) -> None:
    """Drop an asset and everything said inside it from the index."""
    session.execute(
        text(f"DELETE FROM {ASSET_FTS} WHERE asset_id = :asset_id"), {"asset_id": asset_id}
    )
    session.execute(
        text(f"DELETE FROM {SEGMENT_FTS} WHERE asset_id = :asset_id"), {"asset_id": asset_id}
    )
    session.commit()


def index_segments(session: Session, asset_id: str, segments: Iterable[TranscriptSegment]) -> int:
    """Replace this asset's transcript index wholesale.

    Replacement rather than upsert because that is what a re-run does: the segment set
    changes shape, and reconciling row by row would leave orphans from the previous run.
    """
    session.execute(
        text(f"DELETE FROM {SEGMENT_FTS} WHERE asset_id = :asset_id"), {"asset_id": asset_id}
    )

    rows = [
        {
            "segment_id": segment.id,
            "asset_id": asset_id,
            "user_id": segment.user_id,
            "body": segment.text or "",
            "start_time": segment.start_time or 0.0,
        }
        for segment in segments
        if (segment.text or "").strip()
    ]
    if rows:
        session.execute(
            text(
                f"INSERT INTO {SEGMENT_FTS} (segment_id, asset_id, user_id, body, start_time)"
                " VALUES (:segment_id, :asset_id, :user_id, :body, :start_time)"
            ),
            rows,
        )
    session.commit()
    return len(rows)


# ─── querying ────────────────────────────────────────────────────────────────

# FTS5 treats a pile of characters as query syntax: unbalanced quotes, a bare `*`, or
# `NEAR(` raise rather than returning nothing. A user typing into a search box is not
# writing a query language, so every token is quoted and the operators are dropped.
_TOKEN = re.compile(r"[\w']+", re.UNICODE)


def build_match_query(query: str, *, prefix_last: bool = True) -> str:
    """Turn free text into an FTS5 MATCH expression, or "" if there is nothing to match.

    `prefix_last` makes the final token a prefix search, so results narrow while the
    user is still typing rather than vanishing between whole words.
    """
    tokens = _TOKEN.findall(query or "")
    if not tokens:
        return ""

    quoted = [f'"{token}"' for token in tokens[:-1]]
    last = tokens[-1]
    quoted.append(f'"{last}"*' if prefix_last else f'"{last}"')
    return " ".join(quoted)


def search_assets(session: Session, user_id: str, query: str, limit: int = 50) -> list[FtsHit]:
    match = build_match_query(query)
    if not match:
        return []

    rows = session.execute(
        text(
            f"SELECT asset_id, bm25({ASSET_FTS}) AS score,"
            f"       snippet({ASSET_FTS}, 2, '', '', '…', 12) AS excerpt"
            f"  FROM {ASSET_FTS}"
            f" WHERE {ASSET_FTS} MATCH :match AND user_id = :user_id"
            "  ORDER BY score LIMIT :limit"
        ),
        {"match": match, "user_id": user_id, "limit": limit},
    ).all()

    return [FtsHit(asset_id=r[0], score=r[1], snippet=r[2] or "") for r in rows]


def search_segments(session: Session, user_id: str, query: str, limit: int = 50) -> list[FtsHit]:
    """Spoken-word hits, each carrying the second it was said at.

    That timestamp is the point of the feature: FR 10.1.4 asks for the moment, and an
    asset id alone would leave the user scrubbing a ninety-minute file by hand.
    """
    match = build_match_query(query)
    if not match:
        return []

    rows = session.execute(
        text(
            f"SELECT segment_id, asset_id, start_time, bm25({SEGMENT_FTS}) AS score,"
            f"       snippet({SEGMENT_FTS}, 3, '«', '»', '…', 16) AS excerpt"
            f"  FROM {SEGMENT_FTS}"
            f" WHERE {SEGMENT_FTS} MATCH :match AND user_id = :user_id"
            "  ORDER BY score LIMIT :limit"
        ),
        {"match": match, "user_id": user_id, "limit": limit},
    ).all()

    return [
        FtsHit(
            segment_id=r[0],
            asset_id=r[1],
            start_time=r[2],
            score=r[3],
            snippet=r[4] or "",
        )
        for r in rows
    ]
