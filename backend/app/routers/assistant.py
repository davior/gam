"""The assistant: saved conversations, turns run in the background, proposals to apply.

Only with `AI_BACKEND=suite`. The assistant needs a model that calls tools and this app
keeps no model of its own for it (local providers are enrichment's, single-prompt), so in
`local` mode every route that would start a turn answers `assistant_needs_suite` and
`GET /status` says so, for the panel to show instead of a text box that cannot work.
Reading and deleting saved conversations always works.
"""

from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, col, delete, func, select

from app import suite
from app.assistant import apply as apply_service
from app.assistant import worker
from app.assistant.worker import queue
from app.auth import CurrentUser
from app.clock import utcnow
from app.database import get_session
from app.jobs.runner import ACTIVE_STATUSES
from app.models.asset import Asset
from app.models.assistant import AssistantSession, AssistantTurn
from app.providers.base import ProviderError
from app.schemas import DataResponse, ListResponse

router = APIRouter()

MAX_PROMPT = 8_000


def _error(code: int, name: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": name, "message": message})


# ─── shapes ──────────────────────────────────────────────────────────────────


class AssistantStatus(BaseModel):
    # False in `local` mode, where there is no assistant to offer.
    available: bool
    # Whether a turn could start right now; `message` says what is missing when not.
    ready: bool
    model: Optional[str] = None
    message: Optional[str] = None


class TurnRead(BaseModel):
    id: str
    session_id: str
    status: str
    stage: str = ""
    detail: str = ""
    error: Optional[str] = None
    prompt: str
    focus_asset_id: Optional[str] = None
    persona_id: Optional[str] = None
    steps: List[dict] = []
    reply: str = ""
    proposal: Optional[dict] = None
    outcome: str = ""
    cost: Optional[float] = None
    created_at: str


class SessionRead(BaseModel):
    id: str
    title: str
    updated_at: str
    created_at: str


class SessionDetail(SessionRead):
    messages: List[dict]
    turns: List[TurnRead]


class TurnCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT)
    focus_asset_id: Optional[str] = None
    # One of the suite's personas (`GET /personas`), or none. The suite is the authority on
    # whether it exists and may be used: an unknown one ends the turn with its sentence.
    persona_id: Optional[str] = Field(default=None, max_length=64)


class ApplyBody(BaseModel):
    # Apply only these assets' entries. Absent: all of them.
    asset_ids: Optional[List[str]] = None


class ItemResult(BaseModel):
    asset_id: str
    asset_name: str
    applied: List[str]
    skipped: List[str]


def _turn_read(row: AssistantTurn) -> TurnRead:
    return TurnRead(
        id=row.id, session_id=row.session_id, status=row.status, stage=row.stage,
        detail=row.detail, error=row.error_message, prompt=row.prompt,
        focus_asset_id=row.focus_asset_id, persona_id=row.persona_id, steps=json.loads(row.steps or "[]"),
        reply=row.reply, proposal=apply_service.decode(row.proposal), outcome=row.outcome,
        cost=row.cost, created_at=row.created_at.isoformat())


def _session_read(row: AssistantSession) -> SessionRead:
    return SessionRead(id=row.id, title=row.title, updated_at=row.updated_at.isoformat(),
                       created_at=row.created_at.isoformat())


def _owned_session(session: Session, user_id: str, session_id: str) -> AssistantSession:
    row = session.get(AssistantSession, session_id)
    # Someone else's is reported as missing, as everywhere else in this API.
    if row is None or row.user_id != user_id:
        raise _error(404, "not_found", "No such conversation.")
    return row


def _owned_turn(session: Session, user_id: str, turn_id: str) -> AssistantTurn:
    row = session.get(AssistantTurn, turn_id)
    if row is None or row.user_id != user_id:
        raise _error(404, "not_found", "No such turn.")
    return row


# ─── status ──────────────────────────────────────────────────────────────────


@router.get("/status", response_model=DataResponse[AssistantStatus])
def get_status(user: CurrentUser) -> DataResponse[AssistantStatus]:
    if not suite.enabled():
        return DataResponse(data=AssistantStatus(
            available=False, ready=False,
            message="The assistant runs on Gecko Suite. Set AI_BACKEND=suite to turn it on."))
    try:
        model = suite.build_llm(user.id, suite.ASSISTANT_TASK)
    except ProviderError as exc:
        return DataResponse(data=AssistantStatus(available=True, ready=False, message=str(exc)))
    if model is None:
        return DataResponse(data=AssistantStatus(
            available=True, ready=False,
            message="No AI model is set up for the assistant. Choose one in Gecko Suite (Routing)."))
    if not model.supports_tools:
        return DataResponse(data=AssistantStatus(
            available=True, ready=False, model=model.label,
            message=f"{model.label} can't use tools, which the assistant needs. "
                    "Route chat.assistant to another model in Gecko Suite."))
    return DataResponse(data=AssistantStatus(available=True, ready=True, model=model.label))


def _require_ready(user_id: str) -> None:
    """Refuse to start a turn that is certain to fail, with the reason."""
    if not suite.enabled():
        raise _error(409, "assistant_needs_suite",
                     "The assistant runs on Gecko Suite. Set AI_BACKEND=suite to turn it on.")
    try:
        model = suite.build_llm(user_id, suite.ASSISTANT_TASK)
    except ProviderError as exc:
        raise _error(503, "ai_unavailable", f"{exc} Everything else still works.") from exc
    if model is None:
        raise _error(409, "no_provider", "No AI model is set up for the assistant. "
                     "Choose one in Gecko Suite (Routing).")
    if not model.supports_tools:
        raise _error(409, "model_cannot_use_tools",
                     f"{model.label} can't use tools, which the assistant needs. "
                     "Route chat.assistant to another model in Gecko Suite.")


class PersonaRead(BaseModel):
    id: str
    name: str
    description: str = ""
    avatar: Optional[str] = None


@router.get("/personas", response_model=ListResponse[PersonaRead])
def list_personas(user: CurrentUser) -> ListResponse[PersonaRead]:
    """The personas the person may ask as: the suite's, for GAM and for every app.

    Only what a picker shows (never the system prompt). Empty when the suite has none or is
    not there, and then the panel simply has no picker.
    """
    if not suite.enabled():
        return ListResponse(data=[], total=0, limit=0, offset=0)
    rows = [PersonaRead(id=p["id"], name=p["name"], description=p.get("description") or "",
                        avatar=p.get("avatar")) for p in suite.personas(user.id)
            if isinstance(p, dict) and p.get("id") and p.get("name")]
    return ListResponse(data=rows, total=len(rows), limit=len(rows), offset=0)


# ─── conversations ───────────────────────────────────────────────────────────


@router.get("/sessions", response_model=ListResponse[SessionRead])
def list_sessions(user: CurrentUser, limit: int = 50, offset: int = 0,
                  session: Session = Depends(get_session)) -> ListResponse[SessionRead]:
    limit = max(1, min(limit, 200))
    where = AssistantSession.user_id == user.id
    total = session.exec(select(func.count()).select_from(AssistantSession).where(where)).one()
    rows = session.exec(select(AssistantSession).where(where)
                        .order_by(col(AssistantSession.updated_at).desc())
                        .offset(max(0, offset)).limit(limit)).all()
    return ListResponse(data=[_session_read(r) for r in rows], total=total, limit=limit,
                        offset=max(0, offset))


@router.post("/sessions", response_model=DataResponse[SessionRead], status_code=201)
def create_session(user: CurrentUser,
                   session: Session = Depends(get_session)) -> DataResponse[SessionRead]:
    import uuid

    row = AssistantSession(id=str(uuid.uuid4()), user_id=user.id)
    session.add(row)
    session.commit()
    session.refresh(row)
    return DataResponse(data=_session_read(row))


@router.get("/sessions/{session_id}", response_model=DataResponse[SessionDetail])
def get_session_detail(session_id: str, user: CurrentUser,
                       session: Session = Depends(get_session)) -> DataResponse[SessionDetail]:
    row = _owned_session(session, user.id, session_id)
    turns = session.exec(select(AssistantTurn).where(AssistantTurn.session_id == row.id)
                         .order_by(col(AssistantTurn.created_at))).all()
    return DataResponse(data=SessionDetail(
        **_session_read(row).model_dump(), messages=json.loads(row.messages or "[]"),
        turns=[_turn_read(t) for t in turns]))


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, user: CurrentUser,
                   session: Session = Depends(get_session)) -> Response:
    row = _owned_session(session, user.id, session_id)
    turns = session.exec(select(AssistantTurn).where(AssistantTurn.session_id == row.id)).all()
    for turn in turns:
        if turn.status in ACTIVE_STATUSES:
            queue.cancel(turn.id)
    session.exec(delete(AssistantTurn).where(AssistantTurn.session_id == row.id))
    session.delete(row)
    session.commit()
    return Response(status_code=204)


# ─── turns ───────────────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/turns", response_model=DataResponse[TurnRead],
             status_code=202)
def start_turn(session_id: str, body: TurnCreate, user: CurrentUser,
               session: Session = Depends(get_session)) -> DataResponse[TurnRead]:
    chat = _owned_session(session, user.id, session_id)
    prompt = body.prompt.strip()
    if not prompt:
        raise _error(400, "bad_request", "Say what you would like to know or change.")
    _require_ready(user.id)
    busy = session.exec(select(AssistantTurn).where(
        AssistantTurn.session_id == chat.id,
        col(AssistantTurn.status).in_(ACTIVE_STATUSES))).first()
    if busy is not None:
        raise _error(409, "turn_running", "The assistant is still answering the last message.")
    focus = None
    if body.focus_asset_id:
        asset = session.get(Asset, body.focus_asset_id)
        # Not an error when it isn't theirs: the turn simply has nothing in view.
        focus = asset.id if asset is not None and asset.user_id == user.id else None
    turn = worker.submit(session, chat, user_id=user.id, prompt=prompt, focus_asset_id=focus,
                         persona_id=body.persona_id or None)
    return DataResponse(data=_turn_read(turn))


@router.get("/turns/{turn_id}", response_model=DataResponse[TurnRead])
def get_turn(turn_id: str, user: CurrentUser,
             session: Session = Depends(get_session)) -> DataResponse[TurnRead]:
    return DataResponse(data=_turn_read(_owned_turn(session, user.id, turn_id)))


@router.post("/turns/{turn_id}/cancel", response_model=DataResponse[TurnRead])
def cancel_turn(turn_id: str, user: CurrentUser,
                session: Session = Depends(get_session)) -> DataResponse[TurnRead]:
    turn = _owned_turn(session, user.id, turn_id)
    if turn.status in ACTIVE_STATUSES:
        queue.cancel(turn.id)
    return DataResponse(data=_turn_read(turn))


def _pending(turn: AssistantTurn) -> dict:
    proposal = apply_service.decode(turn.proposal)
    if proposal is None or turn.outcome != "pending":
        raise _error(409, "nothing_to_apply",
                     "This turn has no proposal waiting"
                     + (f" (it was {turn.outcome})." if turn.outcome else "."))
    return proposal


@router.post("/turns/{turn_id}/apply", response_model=DataResponse[List[ItemResult]])
def apply_turn(turn_id: str, user: CurrentUser, body: ApplyBody = ApplyBody(),
               session: Session = Depends(get_session)) -> DataResponse[List[ItemResult]]:
    """Make the proposed changes (all of them, or those for `asset_ids`).

    Applying twice would repeat a rename over an edit made in between, so the proposal is
    marked applied before anything is written and a second request is refused. A failure
    part-way is reported by item rather than rolled back: each asset's change is its own
    commit, as it is for a person doing them one by one.
    """
    turn = _owned_turn(session, user.id, turn_id)
    proposal = _pending(turn)
    turn.outcome = "applied"
    turn.updated_at = utcnow()
    session.add(turn)
    session.commit()
    results = apply_service.apply(session, user.id, proposal, body.asset_ids)
    return DataResponse(data=[ItemResult(**r) for r in results])


@router.post("/turns/{turn_id}/discard", response_model=DataResponse[TurnRead])
def discard_turn(turn_id: str, user: CurrentUser,
                 session: Session = Depends(get_session)) -> DataResponse[TurnRead]:
    turn = _owned_turn(session, user.id, turn_id)
    _pending(turn)
    turn.outcome = "discarded"
    turn.updated_at = utcnow()
    session.add(turn)
    session.commit()
    session.refresh(turn)
    return DataResponse(data=_turn_read(turn))
