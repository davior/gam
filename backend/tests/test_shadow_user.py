"""The local record of a Gecko Notes account.

GAM does not own identity, but a verified token is not a user row — and without one it
knows a subject string and nothing else.
"""

from datetime import timedelta

from app.auth import UserCtx
from app.clock import utcnow
from app.models.user import User
from app.services.users import LAST_SEEN_REFRESH, ensure_user
from tests.test_auth import make_token


def test_first_visit_creates_the_row(client, session):
    assert session.get(User, "notes-user-1") is None

    response = client.get("/api/me", headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 200

    user = session.get(User, "notes-user-1")
    assert user is not None
    assert user.username == "davior"
    assert user.last_seen is not None


def test_the_id_is_the_token_subject(client, session):
    """It must equal the id Notes assigned, or the same person becomes two users."""
    client.get(
        "/api/me", headers={"Authorization": f"Bearer {make_token(sub='notes-user-99')}"}
    )
    assert session.get(User, "notes-user-99") is not None


def test_a_second_visit_does_not_duplicate(client, session):
    for _ in range(3):
        client.get("/api/me", headers={"Authorization": f"Bearer {make_token()}"})

    from sqlmodel import select

    assert len(session.exec(select(User)).all()) == 1


def test_a_rename_in_notes_propagates(client, session):
    """Notes is the source of truth for the name, so a rename there lands here the next
    time the user arrives carrying a token that says so."""
    client.get("/api/me", headers={"Authorization": f"Bearer {make_token()}"})

    client.get(
        "/api/me",
        headers={"Authorization": f"Bearer {make_token(username='renamed')}"},
    )

    session.expire_all()
    assert session.get(User, "notes-user-1").username == "renamed"


def test_last_seen_is_not_rewritten_on_every_call(session):
    """Without a threshold this is a write per request — pointless, since nothing needs
    minute-accurate presence, and SQLite has one writer."""
    ctx = UserCtx(id="u1", username="dave")
    ensure_user(session, ctx)

    first = session.get(User, "u1").last_seen
    ensure_user(session, ctx)

    assert session.get(User, "u1").last_seen == first


def test_a_stale_last_seen_is_refreshed(session):
    ctx = UserCtx(id="u1", username="dave")
    ensure_user(session, ctx)

    user = session.get(User, "u1")
    user.last_seen = utcnow() - LAST_SEEN_REFRESH - timedelta(minutes=1)
    session.add(user)
    session.commit()
    stale = user.last_seen

    ensure_user(session, ctx)
    session.expire_all()
    assert session.get(User, "u1").last_seen > stale


def test_me_reports_the_stored_record(client):
    body = client.get(
        "/api/me", headers={"Authorization": f"Bearer {make_token()}"}
    ).json()["data"]

    assert body["id"] == "notes-user-1"
    assert body["username"] == "davior"
    assert body["is_admin"] is False
    assert body["first_seen"]


def test_a_token_with_no_username_still_creates_a_row(client, session):
    """The claim is optional in principle; a missing name must not cost the record."""
    client.get(
        "/api/me", headers={"Authorization": f"Bearer {make_token(username='')}"}
    )

    user = session.get(User, "notes-user-1")
    assert user is not None
    assert user.username == ""
