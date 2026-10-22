"""Read-only mode: the archive copy run against the NAS mirror.

What these pin is that the archive cannot change anything and cannot be talked into
signing somebody else in — the two properties an operator relies on when they point it
at the only other copy of their library.
"""

import sqlite3

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import create_engine

from app.config import Settings, settings
from app.database import read_only_url
from app.models.user import User


@pytest.fixture
def read_only(monkeypatch):
    monkeypatch.setattr(settings, "read_only", True)


@pytest.mark.parametrize(
    "method, path",
    [
        ("post", "/api/tags"),
        ("patch", "/api/assets/some-id"),
        ("delete", "/api/assets/some-id"),
        ("put", "/api/settings/generation"),
    ],
)
def test_every_write_is_refused(auth_client, read_only, method, path):
    response = auth_client.request(method.upper(), path, json={})

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "read_only"


def test_reads_still_work(auth_client, read_only):
    assert auth_client.get("/api/assets").status_code == 200


def test_writes_are_untouched_when_not_read_only(auth_client):
    """The guard must not leak into a normal deployment."""
    # Refused for the body it sent, by the router — not by the guard.
    assert auth_client.post("/api/tags", json={}).status_code == 422


def test_config_says_it_is_read_only(client, read_only):
    assert client.get("/api/config").json()["data"]["read_only"] is True


def test_the_archive_acts_as_its_user_by_id(client, session, read_only, monkeypatch):
    session.add(User(id="owner-id", username="owner"))
    session.commit()
    monkeypatch.setattr(settings, "read_only_user", "owner-id")

    data = client.get("/api/me").json()["data"]

    assert (data["id"], data["username"]) == ("owner-id", "owner")


def test_the_archive_acts_as_its_user_by_username(client, session, read_only, monkeypatch):
    session.add(User(id="owner-id", username="owner"))
    session.commit()
    monkeypatch.setattr(settings, "read_only_user", "owner")

    assert client.get("/api/me").json()["data"]["id"] == "owner-id"


def test_a_token_cannot_choose_someone_else(client, session, read_only, monkeypatch):
    """Whatever arrives in the header, the archive is its one configured user."""
    monkeypatch.setattr(settings, "read_only_user", "owner-id")

    response = client.get("/api/me", headers={"Authorization": "Bearer anything"})

    assert response.json()["data"]["id"] == "owner-id"


def test_read_only_user_alone_does_not_disable_sign_in(client, monkeypatch):
    """Belt to the validator's braces: patched in directly, it still does nothing."""
    monkeypatch.setattr(settings, "read_only_user", "owner-id")

    assert client.get("/api/me").status_code == 401


def test_settings_refuse_read_only_user_without_read_only(monkeypatch):
    for name in ("READ_ONLY", "READ_ONLY_USER"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, jwt_secret_key="a" * 64, read_only_user="owner")

    allowed = Settings(
        _env_file=None, jwt_secret_key="a" * 64, read_only=True, read_only_user="owner"
    )
    assert allowed.read_only_user == "owner"


def test_the_read_only_engine_reads_and_cannot_write(tmp_path):
    path = tmp_path / "gam.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE t (x INTEGER)")
        db.execute("INSERT INTO t VALUES (1)")

    engine = create_engine(read_only_url(f"sqlite:///{path}"))
    with engine.connect() as connection:
        assert connection.execute(text("SELECT x FROM t")).scalar() == 1
        with pytest.raises(OperationalError, match="readonly"):
            connection.execute(text("INSERT INTO t VALUES (2)"))


def test_read_only_url_leaves_other_databases_alone():
    assert read_only_url("postgresql://x/y") == "postgresql://x/y"
