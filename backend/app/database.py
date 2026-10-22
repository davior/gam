"""Engine, session factory and the SQLite pragmas that make concurrent reads work.

Schema changes are Alembic migrations (`alembic/versions/`), not `create_all` and not
a sequence of guarded `ALTER TABLE` statements. gecko-notes took the latter route and
ended up with 650 lines of `try: … except: pass` carrying data backfills, no version
table and no way back — this app starts on the other side of that decision.
"""

import logging
from pathlib import Path
from typing import Iterator

from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlmodel import Session, create_engine

from app.config import settings

logger = logging.getLogger(__name__)

DATABASE_URL = settings.resolved_database_url


def read_only_url(url: str) -> str:
    """The same SQLite file, opened so that no write can reach it.

    `mode=ro` rather than relying on the `:ro` bind mount alone: on a read-only mount
    SQLite still opens read-write and fails on the first write with "attempt to write a
    readonly database" — after the request has already done half its work. `mode=ro`
    fails it at the statement, and keeps a mirror mounted read-write safe as well.

    Needs a file in rollback-journal mode. A WAL file opened `mode=ro` still has to
    create its `-shm`, which a read-only mount cannot hold; the mirror converts its
    copy before uploading it (ops/backup/backup.sh).
    """
    if not url.startswith("sqlite:///"):
        return url
    path = url.replace("sqlite:///", "", 1)
    return f"sqlite:///file:{path}?mode=ro&uri=true"


if settings.read_only:
    DATABASE_URL = read_only_url(DATABASE_URL)
elif DATABASE_URL.startswith("sqlite:///"):
    Path(DATABASE_URL.replace("sqlite:///", "", 1)).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    # SQLite refuses a connection used from a thread other than the one that opened
    # it. Background workers run on their own threads and open their own sessions,
    # so the check has to come off.
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    # A read-only archive's database is replaced under it on every mirror run, by a
    # rename. A pooled connection holds the old file open and goes on reading it, so
    # the archive would show the day it started until somebody restarted it. A fresh
    # connection per session costs a file open, which SQLite does in microseconds.
    **({"poolclass": NullPool} if settings.read_only else {}),
    echo=False,
)


if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        """WAL so a long read (streaming a video's metadata) does not block a write.

        `synchronous=NORMAL` is the usual companion: with WAL it still survives an
        application crash, and only risks the last transactions on a power loss —
        which is the right trade for a single-host media library.
        """
        cursor = dbapi_connection.cursor()
        # Both are writes to the file header, so a read-only connection refuses them.
        if not settings.read_only:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
