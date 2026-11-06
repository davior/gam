"""Writing a note into Gecko Notes, as the person who asked for it.

Only one thing is done here: file a note the assistant proposed, once the person has
pressed Apply. The person's own token is forwarded (Notes verifies it with the shared
`JWT_SECRET_KEY`), so the note is theirs and Notes' own checks apply. GAM holds no
credential of its own for Notes.

Notes turns the Markdown into its blocks, picks the category and makes the "From GAM"
folder (gecko-notes `POST /api/notes/from-markdown`), so nothing here knows BlockNote's
format.

Every failure is a `NotesError` whose message is a sentence to show: an Apply of
several things reports a note that could not be made beside the rest, never instead of it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Swapped for an httpx.MockTransport in tests.
TRANSPORT: Optional[httpx.BaseTransport] = None
TIMEOUT_SECONDS = 20.0


class NotesError(Exception):
    pass


@dataclass
class CreatedNote:
    id: str
    title: str
    url: str


def create_note(token: Optional[str], *, title: str, markdown: str,
                folder_name: Optional[str] = None, tags: Optional[List[str]] = None) -> CreatedNote:
    if not token:
        # The dev user signs in without a token, and Notes has no one to file the note for.
        raise NotesError("Sign in through Gecko Notes to create notes.")
    base = settings.notes_url.rstrip("/")
    body = {"title": title, "markdown": markdown, "folder_name": folder_name,
            "tags": tags or [], "source_app": "gam"}
    try:
        with httpx.Client(base_url=base, timeout=TIMEOUT_SECONDS, transport=TRANSPORT,
                          headers={"Authorization": f"Bearer {token}"}) as client:
            response = client.post("/api/notes/from-markdown", json=body)
    except httpx.HTTPError as exc:
        logger.warning("Gecko Notes unreachable at %s: %s", base, exc)
        raise NotesError("Gecko Notes can't be reached right now. Apply again later.") from exc
    if response.status_code == 401:
        raise NotesError("Gecko Notes did not accept your session. GAM and Notes probably "
                         "have different JWT_SECRET_KEY values.")
    if response.status_code != 201:
        # Where it went and what came back, every time: "needs updating" alone sent a
        # person who had just updated Notes looking for a fault that was in the address.
        logger.warning("Gecko Notes at %s answered %s to a new note", base, response.status_code)
    if response.status_code in (404, 405):
        # A Notes from before the endpoint (gecko-notes#224) answers 405: it has
        # GET/PUT/DELETE at /api/notes/{id} and takes "from-markdown" for an id. So does
        # anything else at that address, which is why the address is in the sentence.
        raise NotesError(
            f"Gecko Notes at {base} answered {response.status_code} to a new note: that "
            "Notes is older than the update that takes notes from GAM, or NOTES_URL "
            "points at the wrong one.")
    if response.status_code != 201:
        message = None
        try:
            detail = response.json().get("detail")
            message = detail.get("message") if isinstance(detail, dict) else None
        except ValueError:
            pass
        raise NotesError(message or f"Gecko Notes refused the note ({response.status_code}).")
    note = response.json()["data"]
    return CreatedNote(id=note["id"], title=note.get("title") or title,
                       url=f"{base}/notes/{note['id']}")
