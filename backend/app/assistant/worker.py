"""Running assistant turns in the background.

A turn is called on the suite as GAM, for the person who asked (`SUITE_APP_SECRET`), like
enrichment: there is no session token to hold, so unlike GVC's assistant a turn that a
restart interrupted is simply run again from its prompt (the queue requeues it).

Everything a person sees lives on the `AssistantTurn` row as it happens, so a turn can
finish with nobody watching and be waiting, with its proposal, when they come back.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import closing
from typing import List, Optional, Tuple

from gecko_suite_client import Stopped, run_agent
from sqlmodel import Session, func, select

from app import suite
from app.assistant import prompt as prompts
from app.assistant import tools as T
from app.clock import utcnow
from app.config import settings
from app.database import engine
from app.jobs.runner import JobQueue, readable_error, set_fields
from app.models.asset import Asset
from app.models.assistant import AssistantSession, AssistantTurn

logger = logging.getLogger(__name__)

MAX_STEPS_KEPT = 200

OUTPUT_LIMIT = ("The reply hit the model's output limit. Ask for less at once, or raise its "
                "maximum response length in Gecko Suite.")


def append_message(row: AssistantSession, role: str, text: str,
                   turn_id: Optional[str] = None) -> None:
    messages = json.loads(row.messages or "[]")
    messages.append({"role": role, "text": text, "turn_id": turn_id,
                     "at": utcnow().isoformat(timespec="seconds")})
    row.messages = json.dumps(messages, ensure_ascii=False)
    row.updated_at = utcnow()


def history_for(row: AssistantSession, turn_id: str) -> List[Tuple[str, str]]:
    """Earlier messages, newest kept, with this turn's own prompt left out (it is sent as
    the new message)."""
    messages = [m for m in json.loads(row.messages or "[]")
                if m.get("turn_id") != turn_id and m.get("text")
                and m.get("role") in ("user", "assistant")]
    return [(m["role"], m["text"]) for m in messages[-settings.assistant_history_messages:]]


def _run(turn_id: str) -> None:
    with queue.session() as session:
        turn = session.get(AssistantTurn, turn_id)
        if turn is None or turn.status != "queued":
            return
        if queue.is_cancelled(turn_id):
            # Stopped before it started.
            set_fields(session, turn, status="cancelled", stage="")
            return
        # A requeued turn starts over, so nothing the interrupted run wrote is left to
        # mislead.
        set_fields(session, turn, status="processing", stage="Thinking", progress=0,
                   steps="[]", reply="", proposal=None, outcome="", cost=None)
        chat = session.get(AssistantSession, turn.session_id)
        user_id, prompt, focus_id = turn.user_id, turn.prompt, turn.focus_asset_id
        history = history_for(chat, turn_id) if chat else []
        chat_id = turn.session_id
        focus = None
        if focus_id:
            asset = session.get(Asset, focus_id)
            if asset is not None and asset.user_id == user_id:
                focus = {"id": asset.id, "name": asset.name, "asset_type": asset.asset_type}
        size = session.exec(select(func.count()).select_from(Asset)
                            .where(Asset.user_id == user_id)).one()

    steps: List[dict] = []
    spent: List[float] = []

    def write(**fields) -> None:
        with queue.session() as session:
            row = session.get(AssistantTurn, turn_id)
            if row is not None:
                set_fields(session, row, **fields)

    def on_step(kind: str, text: str) -> None:
        steps.append({"kind": kind, "text": text[:600]})
        write(steps=json.dumps(steps[-MAX_STEPS_KEPT:], ensure_ascii=False), stage="Working",
              detail={"search": "searching", "read": "reading", "propose": "proposing",
                      "think": "thinking"}.get(kind, ""))

    def on_reply(reply) -> None:
        # The suite wrote this call to its own ledger, under this app and this
        # conversation. Only the turn's running total is kept here.
        if reply.cost is not None:
            spent.append(reply.cost)
            write(cost=round(sum(spent), 6))

    working = T.Working(user_id, queue.session, on_step)
    try:
        with closing(suite.client(user_id)) as connection:
            chat_model = suite.assistant_chat(connection, f"gam:assistant:{chat_id}")
            model = chat_model.resolve()
            if model is None:
                raise suite.ProviderError("No AI model is set up for the assistant. Choose "
                                          "one in Gecko Suite (Routing).")
            if "tools" not in model.get("capabilities", []):
                raise suite.ProviderError(
                    f"{model.get('label') or model.get('model_id')} can't use tools, which the "
                    "assistant needs. Route chat.assistant to another model in Gecko Suite.")
            conversation = chat_model.conversation(
                system=[prompts.REFERENCE, prompts.context(focus=focus, library_size=size)],
                tools=T.SPECS, history=history, prompt=prompt)
            outcome = run_agent(
                conversation,
                handlers=working.handlers,
                max_tokens_message=OUTPUT_LIMIT,
                cancelled=lambda: queue.is_cancelled(turn_id),
                on_reply=on_reply, on_step=on_step,
                max_rounds=settings.assistant_max_rounds)
    except Stopped:
        _finish(turn_id, status="cancelled", stage="", message="_Stopped._", steps=steps)
        return
    except suite.SuiteError as exc:
        # The suite said no or was not there. Its sentence is written to be shown, and
        # the rest of the app is unaffected: only this turn ends.
        sentence = suite.sentence(exc)
        _finish(turn_id, status="error", stage="", error_message=sentence,
                message=f"_{sentence}_", steps=steps)
        return
    except suite.ProviderError as exc:
        _finish(turn_id, status="error", stage="", error_message=str(exc),
                message=f"_{exc}_", steps=steps)
        return
    except Exception as exc:  # noqa: BLE001 - a failed turn is a job error, not a crash
        logger.exception("Assistant turn %s failed", turn_id)
        _finish(turn_id, status="error", stage="", error_message=readable_error(exc),
                message="_Something went wrong. Ask again._", steps=steps)
        return

    proposal = working.proposal_json()
    reply = outcome.reply or ("Done." if proposal else "")
    if outcome.cut_short:
        reply = (reply + "\n\n" if reply else "") + f"_{outcome.cut_short}_"
    _finish(turn_id, status="done", stage="", progress=100, detail="", message=reply,
            steps=steps, reply=reply, proposal=proposal, outcome="pending" if proposal else "")


def _finish(turn_id: str, *, message: str, steps: List[dict], **fields) -> None:
    with queue.session() as session:
        turn = session.get(AssistantTurn, turn_id)
        if turn is None:
            return
        set_fields(session, turn, steps=json.dumps(steps[-MAX_STEPS_KEPT:], ensure_ascii=False),
                   **fields)
        chat = session.get(AssistantSession, turn.session_id)
        if chat is not None and message:
            append_message(chat, "assistant", message, turn_id)
            session.add(chat)
            session.commit()


queue = JobQueue(AssistantTurn, _run, name="assistant",
                 concurrency=settings.assistant_concurrency, engine=engine)


def submit(session: Session, chat: AssistantSession, *, user_id: str, prompt: str,
           focus_asset_id: Optional[str]) -> AssistantTurn:
    turn = AssistantTurn(id=str(uuid.uuid4()), session_id=chat.id, user_id=user_id,
                         prompt=prompt, focus_asset_id=focus_asset_id)
    append_message(chat, "user", prompt, turn.id)
    if not chat.title:
        chat.title = prompt.strip().splitlines()[0][:80]
    session.add(turn)
    session.add(chat)
    session.commit()
    session.refresh(turn)
    queue.enqueue(turn.id)
    return turn


def start() -> None:
    queue.start()
