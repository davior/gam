"""The boot-time warning that the database is behind.

This exists because the alternative is how it was found: a pull added M3's tables, the
dev server had never run `alembic upgrade head` (only `entrypoint.sh` does, and that is
Docker-only), and every request came back as several hundred lines of
`sqlite3.OperationalError: no such table: tag` that never mentioned migrations.
"""

import logging
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlmodel import create_engine

from app import schema_check

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.attributes["configure_logger"] = False
    # Leave this process's logging alone — see the comment in alembic/env.py. Without
    # it, running a migration removes pytest's capture handler and every assertion
    # about silence below passes for entirely the wrong reason. The first draft did.
    return config


def _revisions_newest_first():
    script = ScriptDirectory.from_config(_config())
    return list(script.iterate_revisions(script.get_current_head(), "base"))


@pytest.fixture(name="db")
def db_fixture(tmp_path, monkeypatch):
    """A real temp database, and a `migrate(rev)` that leaves logging intact.

    Same shape as `test_migrations.py`: env.py builds its own engine from
    `app.database`, so both it and settings have to point at the temp file.

    Migrations run through `_config()`, which opts out of alembic's logging setup —
    without that, `command.upgrade` silences the very logger these tests assert on.
    """
    db_path = tmp_path / "schema.db"
    url = f"sqlite:///{db_path}"

    from app import database
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(
        database, "engine", create_engine(url, connect_args={"check_same_thread": False})
    )

    engine = create_engine(url, connect_args={"check_same_thread": False})
    config = _config()

    def migrate(revision: str) -> None:
        command.upgrade(config, revision)

    return engine, migrate


def warnings_from(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_current_database_says_nothing(db, caplog):
    """Load-bearing for Docker, where entrypoint.sh has already migrated: a check that
    warns on a healthy boot is noise nobody reads, and then nobody reads the real one.

    The second half is what makes the first half mean anything — silence has to be the
    *check's* decision, not a logger that was switched off.
    """
    engine, migrate = db
    migrate("head")

    schema_check.log_if_behind(engine)
    assert warnings_from(caplog) == []

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = :r"),
            {"r": _revisions_newest_first()[1].revision},
        )
    schema_check.log_if_behind(engine)
    assert warnings_from(caplog), "the check was mute, so the silence above proved nothing"


def test_it_counts_how_far_behind(db, caplog):
    engine, migrate = db
    # Two migrations back from head, resolved from the chain rather than hardcoded — a
    # pinned revision id would need editing every time a migration is added, and a test
    # that needs editing to keep passing gets deleted instead.
    target = _revisions_newest_first()[2].revision
    migrate(target)

    schema_check.log_if_behind(engine)

    message = "\n".join(warnings_from(caplog))
    assert "2 migrations behind" in message
    assert "alembic upgrade head" in message
    assert target in message


def test_one_behind_is_singular(db, caplog):
    engine, migrate = db
    migrate(_revisions_newest_first()[1].revision)

    schema_check.log_if_behind(engine)

    assert "1 migration behind" in "\n".join(warnings_from(caplog))


def test_a_database_with_no_schema_at_all(db, caplog):
    """The brand-new-clone case: no `alembic_version` table, so there is no revision to
    compare against. Say what to run, not how far behind."""
    engine, _ = db

    schema_check.log_if_behind(engine)

    message = "\n".join(warnings_from(caplog))
    assert "no schema" in message
    assert "alembic upgrade head" in message


def test_a_revision_this_checkout_does_not_know_blames_the_code(db, caplog):
    """Checked out older code against a newer database.

    The remedy is the opposite one, so getting this wrong is worse than staying quiet:
    `alembic upgrade head` has nothing to apply and would look like a fix that failed.
    """
    engine, migrate = db
    migrate("head")
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = 'deadbeef'"))

    schema_check.log_if_behind(engine)

    message = "\n".join(warnings_from(caplog))
    assert "deadbeef" in message
    assert "older than the database" in message
    assert "Do not run" in message


def test_it_never_stops_the_app(db, caplog, monkeypatch):
    """A diagnostic that raises is strictly worse than no diagnostic: it would turn a
    stale dev database into an app that refuses to start at all."""
    engine, _ = db
    monkeypatch.setattr(schema_check, "BACKEND_ROOT", Path("/nonexistent"))

    schema_check.log_if_behind(engine)  # must not raise

    assert warnings_from(caplog) == []
