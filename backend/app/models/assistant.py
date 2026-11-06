"""The assistant's saved conversations and the turns inside them.

A turn is a job (the enrichment queue's pattern), so it keeps running after the panel or
the browser is closed. Everything it is and does lives on its row: the prompt, each step
as it happens, the reply and the proposal it ended with. Someone coming back finds it
running, or finished and waiting for Apply or Discard.

The assistant never edits the library itself. A turn ends with at most one proposal, held
here as JSON, and nothing in `Asset` or `Tag` changes until a person applies it
(routers/assistant.py). That is why the proposal is stored rather than re-derived: what
is applied must be exactly what was shown.
"""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.clock import utcnow


class AssistantSession(SQLModel, table=True):
    __tablename__ = "assistant_session"

    id: str = Field(primary_key=True)
    user_id: str = Field(index=True)
    title: str = ""
    # JSON: [{"role": "user"|"assistant", "text", "turn_id", "at"}]. The transcript the
    # panel shows and the history a later turn replays; the model's own tool exchanges
    # are not kept, only what was said.
    messages: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AssistantTurn(SQLModel, table=True):
    __tablename__ = "assistant_turn"

    id: str = Field(primary_key=True)
    session_id: str = Field(foreign_key="assistant_session.id", index=True)
    user_id: str = Field(index=True)
    # queued | processing | done | error | cancelled (jobs/runner.py)
    status: str = Field(default="queued", index=True)
    stage: str = ""
    progress: int = 0
    detail: str = ""
    error_message: Optional[str] = None
    prompt: str = ""
    # The asset open in the library when the turn was asked, if any: "this" in a request
    # means it.
    focus_asset_id: Optional[str] = None
    # The suite persona this turn was asked as, if any: its system prompt goes first and its
    # model for chat.assistant replaces the routed one. Kept on the turn, not read afresh, so
    # a turn that a restart reruns is asked as the same persona.
    persona_id: Optional[str] = None
    # JSON: [{"kind": "think"|"search"|"read"|"propose"|"issue", "text"}]
    steps: str = "[]"
    reply: str = ""
    # JSON: {"items": [{"asset_id", "asset_name", "name", "description", "summary",
    # "add_tags", "remove_tags", "enrich"}], "clips": [{"clip_id", "kind": "clip"|"cut",
    # "asset_id", "asset_name", "in_point", "out_point", "name", "reason"}]}; None when the
    # turn proposed nothing. `clips` is absent on turns from before clips were proposed.
    # Needs no migration: it is JSON in a text column.
    proposal: Optional[str] = None
    # pending | applied | discarded, once there is a proposal; "" otherwise.
    outcome: str = ""
    cost: Optional[float] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
