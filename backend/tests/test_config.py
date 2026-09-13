"""The settings object, and the guard that stops the app running without a real secret."""

import pytest
from pydantic import ValidationError

from app.config import REPO_ROOT, Settings, settings


def _settings(**overrides) -> Settings:
    """Build a Settings ignoring the repo .env, so a developer's local file cannot
    change what these tests assert."""
    return Settings(_env_file=None, **overrides)


@pytest.mark.parametrize(
    "secret",
    ["", "   ", "change-me", "gecko-notes-secret-change-in-production"],
)
def test_weak_secrets_are_refused(secret):
    with pytest.raises(ValidationError):
        _settings(jwt_secret_key=secret)


def test_strong_secret_is_accepted():
    assert _settings(jwt_secret_key="a" * 64).jwt_secret_key == "a" * 64


def test_database_and_media_defaults_live_under_the_repo():
    s = _settings(jwt_secret_key="a" * 64)
    assert s.resolved_database_url.endswith("data/db/gam.db")
    assert s.resolved_media_dir == REPO_ROOT / "data" / "media"


def test_explicit_paths_win():
    s = _settings(
        jwt_secret_key="a" * 64,
        database_url="sqlite:///tmp/other.db",
        media_dir="/srv/media",
    )
    assert s.resolved_database_url == "sqlite:///tmp/other.db"
    assert str(s.resolved_media_dir) == "/srv/media"


def test_cors_origins_are_split_and_trimmed():
    s = _settings(
        jwt_secret_key="a" * 64,
        cors_origin="https://a.example.com, https://b.example.com ,",
    )
    assert s.cors_origins == ["https://a.example.com", "https://b.example.com"]


def test_cors_origins_empty_by_default():
    assert _settings(jwt_secret_key="a" * 64).cors_origins == []


def test_dev_auth_needs_both_conditions():
    base = {"jwt_secret_key": "a" * 64}
    assert not _settings(**base, dev_auth_user="x", environment="production").dev_auth_enabled
    assert not _settings(**base, dev_auth_user="", environment="development").dev_auth_enabled
    assert _settings(**base, dev_auth_user="x", environment="development").dev_auth_enabled
