"""Shared fixtures.

gecko-notes has none: six of its test modules build their own in-memory engine, and
`TestClient` appears exactly once in the whole suite, so its routers are tested by
calling handler functions directly with a hand-built session. Status codes, response
models and the auth layer are therefore largely unverified there. These fixtures exist
so GAM tests the app through real HTTP from the first commit.

The secret is set before any app module is imported: app.config refuses a weak or
missing JWT_SECRET_KEY at import time, which is the behaviour under test in
test_config.py and would otherwise break collection.
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-not-used-in-production")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.auth import current_user  # noqa: E402
from app.database import get_session  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.auth import UserCtx  # noqa: E402

TEST_USER_ID = "user-under-test"


@pytest.fixture(autouse=True)
def no_background_workers(monkeypatch):
    """Keep the app's worker threads out of the test suite.

    The lifespan starts them, and TestClient runs the lifespan — so without this every
    test that builds a client spawns a sweeper thread polling the *real* engine, which
    in a test run points at a database with no tables. It logs a stream of exceptions
    and, worse, is real background work firing during unrelated tests.

    Tests that want the worker call `_run_job` directly, which is also the only way to
    assert on what it did.
    """
    from app.jobs import enrichment

    monkeypatch.setattr(enrichment, "start", lambda: None)


@pytest.fixture
def anyio_backend():
    """Run `@pytest.mark.anyio` tests on asyncio only.

    anyio's plugin would otherwise parameterise every async test across asyncio and
    trio, and trio is not a dependency — the app runs under uvicorn/asyncio.
    """
    return "asyncio"


@pytest.fixture(name="engine")
def engine_fixture():
    """An in-memory database shared across connections.

    StaticPool is what makes ":memory:" usable here: without it every connection gets
    its own empty database, so a row written through the request's session is invisible
    to a test asserting on it.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)


@pytest.fixture(name="session")
def session_fixture(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session):
    """An unauthenticated client. Requests to a protected route get a real 401."""
    fastapi_app.dependency_overrides[get_session] = lambda: session
    with TestClient(fastapi_app) as client:
        yield client
    fastapi_app.dependency_overrides.clear()


@pytest.fixture(name="auth_client")
def auth_client_fixture(session):
    """A client that is signed in, by overriding the dependency rather than minting a
    token. Tests that care about token *verification* should build one instead — see
    test_auth.py — so this override never hides a broken auth path."""
    fastapi_app.dependency_overrides[get_session] = lambda: session
    fastapi_app.dependency_overrides[current_user] = lambda: UserCtx(
        id=TEST_USER_ID, username="tester"
    )
    with TestClient(fastapi_app) as client:
        yield client
    fastapi_app.dependency_overrides.clear()


@pytest.fixture(name="media_dir")
def media_dir_fixture(tmp_path, monkeypatch):
    """Point storage at a temp directory for one test.

    Patched on the settings object rather than a module global — the reason config.py
    exists. gecko-notes has to do `monkeypatch.setattr(asset_utils, "MEDIA_DIR", ...)`
    on whichever module imported the value first.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "media_dir", str(tmp_path))
    return tmp_path


@pytest.fixture(name="library")
def library_fixture(session, media_dir):
    """A signed-in client whose storage is a temp directory.

    The common case for asset tests: they need both an authenticated caller and a
    storage root that does not touch the developer's real media tree.
    """
    from app.storage import LocalStorage
    from app.routers.assets import get_storage

    storage = LocalStorage(media_dir)
    fastapi_app.dependency_overrides[get_session] = lambda: session
    fastapi_app.dependency_overrides[get_storage] = lambda: storage
    fastapi_app.dependency_overrides[current_user] = lambda: UserCtx(
        id=TEST_USER_ID, username="tester"
    )
    with TestClient(fastapi_app) as client:
        yield client
    fastapi_app.dependency_overrides.clear()
