"""The migration chain applies to an empty database and reverses cleanly.

gecko-notes never exercises its schema setup — its tests call
`SQLModel.metadata.create_all` directly, so the 650-line migration function that
production actually runs is untested, and a broken statement in it would only surface
on deploy. This runs the real chain, which is the whole point of having one.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(name="alembic_config")
def alembic_config_fixture(tmp_path, monkeypatch):
    db_path = tmp_path / "migrate.db"
    url = f"sqlite:///{db_path}"

    # env.py reads the URL from settings and builds its own engine from app.database,
    # so both have to point at the temp database for the run to be isolated.
    from app import database
    from app.config import settings
    from sqlmodel import create_engine

    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(
        database, "engine", create_engine(url, connect_args={"check_same_thread": False})
    )

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return config, db_path


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def test_upgrade_head_creates_the_schema(alembic_config):
    config, db_path = alembic_config
    command.upgrade(config, "head")

    tables = _tables(db_path)
    assert "user" in tables
    assert "alembic_version" in tables


def test_downgrade_base_reverses_it(alembic_config):
    """A migration that cannot be undone is a migration you cannot roll back from."""
    config, db_path = alembic_config
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    assert _tables(db_path) == {"alembic_version"}


def test_upgrade_is_repeatable(alembic_config):
    """Running it twice is what a container restart does."""
    config, _ = alembic_config
    command.upgrade(config, "head")
    command.upgrade(config, "head")


def test_the_migration_and_the_test_ddl_agree(alembic_config):
    """Guards the one place this schema is written twice.

    The migration carries a literal copy of the FTS5 DDL — a migration must describe the
    schema as it was, not import live code that can change under it — and conftest builds
    the same tables from constants so it need not run the chain per test. That is two
    copies, so this asserts the real migration produces exactly the columns those
    constants claim. Without it, a schema change could pass every test against a shape
    production does not have.
    """
    import sqlite3

    from app.search.fts import ASSET_FTS_COLUMNS, SEGMENT_FTS_COLUMNS

    config, db_path = alembic_config
    command.upgrade(config, "head")

    with sqlite3.connect(db_path) as conn:
        for table, expected in (
            ("asset_fts", ASSET_FTS_COLUMNS),
            ("segment_fts", SEGMENT_FTS_COLUMNS),
        ):
            actual = tuple(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))
            assert actual == expected, f"{table}: migration has {actual}, constants say {expected}"


def test_autogenerate_does_not_try_to_drop_the_search_index(alembic_config, monkeypatch):
    """The landmine this guards against was real, not theoretical.

    FTS5 virtual tables cannot appear in SQLModel.metadata, so autogenerate sees them in
    the database, fails to find them in the models, and writes DROP statements. The
    first run after the search index was added produced eleven of them — between them,
    the entire search index. `include_object` in alembic/env.py filters them out.

    This asserts a fresh autogenerate against an up-to-date database produces no
    migration at all, which is the only state in which nobody has to notice the drops
    before committing them.
    """
    from alembic.autogenerate import produce_migrations
    from alembic.migration import MigrationContext
    from sqlmodel import SQLModel, create_engine

    import app.models  # noqa: F401  - registers every table
    from app.search.fts import include_object

    config, db_path = alembic_config
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"include_object": include_object, "compare_type": True}
        )
        diff = produce_migrations(context, SQLModel.metadata)

    operations = diff.upgrade_ops.as_diffs() if diff.upgrade_ops else []
    dropped = [op for op in operations if "remove_table" in str(op[0])]
    assert not dropped, f"autogenerate would drop: {dropped}"
    assert not operations, f"schema and models disagree: {operations}"


def test_the_migrated_schema_enforces_case_insensitive_tag_names(alembic_config):
    """Asserted against a migrated database, because the model metadata lied.

    `Tag.__table_args__` declares a unique index on `(user_id, lower(name))`, and
    conftest builds its tables with `SQLModel.metadata.create_all`, which honours it. But
    Alembic's autogenerate does not emit expression indexes — it silently produced the
    two plain indexes on `tag` and dropped this one. Every unit test would therefore have
    had case-insensitive uniqueness while production had none, and "NATO" and "nato"
    would both have inserted there and nowhere else.

    So this checks the constraint holds where it actually has to, and it checks it by
    inserting rather than by reading the index list: an index that exists but does not
    bite is the same bug wearing a disguise.
    """
    config, db_path = alembic_config
    command.upgrade(config, "head")

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO tag (id, user_id, name, created_at) VALUES ('1', 'u', 'NATO', '2026-01-01')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tag (id, user_id, name, created_at)"
                " VALUES ('2', 'u', 'nato', '2026-01-01')"
            )
        # Scoped per user, not globally: two people may each keep their own "nato".
        conn.execute(
            "INSERT INTO tag (id, user_id, name, created_at)"
            " VALUES ('3', 'other', 'nato', '2026-01-01')"
        )
