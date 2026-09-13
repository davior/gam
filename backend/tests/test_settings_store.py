"""Per-user settings, and the secrets among them."""

import json

from sqlmodel import select

from app.models.setting import UserSetting
from app.settings_store import (
    DEEPGRAM_API_KEY,
    DEEPGRAM_MODEL,
    clear_setting,
    get_setting,
    has_setting,
    load_deepgram_key,
    set_setting,
)


def test_round_trip_a_plain_setting(session):
    set_setting(session, "u1", DEEPGRAM_MODEL, "nova-2")
    assert get_setting(session, "u1", DEEPGRAM_MODEL) == "nova-2"


def test_missing_setting_returns_the_default(session):
    assert get_setting(session, "u1", DEEPGRAM_MODEL, "nova-3") == "nova-3"


def test_settings_are_per_user(session):
    set_setting(session, "u1", DEEPGRAM_MODEL, "nova-2")
    assert get_setting(session, "u2", DEEPGRAM_MODEL) is None


def test_a_secret_is_encrypted_at_rest(session):
    """The database must not hold a usable credential in the clear."""
    set_setting(session, "u1", DEEPGRAM_API_KEY, "dg-super-secret")

    row = session.exec(
        select(UserSetting).where(UserSetting.user_id == "u1", UserSetting.key == DEEPGRAM_API_KEY)
    ).first()
    stored = json.loads(row.value)

    assert "dg-super-secret" not in row.value
    assert stored.startswith("enc:")
    # And it still comes back.
    assert get_setting(session, "u1", DEEPGRAM_API_KEY) == "dg-super-secret"


def test_has_setting_reports_presence_without_revealing(session):
    """What the API tells the browser: a key is configured, never what it is."""
    assert has_setting(session, "u1", DEEPGRAM_API_KEY) is False

    set_setting(session, "u1", DEEPGRAM_API_KEY, "dg-key")
    assert has_setting(session, "u1", DEEPGRAM_API_KEY) is True


def test_updating_a_setting_replaces_it(session):
    set_setting(session, "u1", DEEPGRAM_MODEL, "nova-2")
    set_setting(session, "u1", DEEPGRAM_MODEL, "nova-3")

    rows = session.exec(select(UserSetting).where(UserSetting.user_id == "u1")).all()
    assert len(rows) == 1
    assert get_setting(session, "u1", DEEPGRAM_MODEL) == "nova-3"


def test_clearing_removes_it(session):
    set_setting(session, "u1", DEEPGRAM_API_KEY, "dg-key")
    clear_setting(session, "u1", DEEPGRAM_API_KEY)

    assert load_deepgram_key(session, "u1") is None


def test_an_unreadable_row_reads_as_unset(session):
    """A row written by an older version, or encrypted under a rotated secret, should
    read as "not configured" — which is recoverable — rather than breaking every
    request that touches settings."""
    session.add(UserSetting(user_id="u1", key=DEEPGRAM_MODEL, value="{not json"))
    session.commit()

    assert get_setting(session, "u1", DEEPGRAM_MODEL, "fallback") == "fallback"


def test_a_corrupt_secret_reads_as_unset(session):
    session.add(UserSetting(user_id="u1", key=DEEPGRAM_API_KEY, value=json.dumps("enc:garbage")))
    session.commit()

    assert load_deepgram_key(session, "u1") is None
