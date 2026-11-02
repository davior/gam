"""Gecko Suite as enrichment's model and the embeddings' source (`AI_BACKEND=suite`).

Everything this app knows about the suite is here. The seam is two functions the rest of
the code already calls, `build_provider` and `build_embedder`, which return objects with
the same small interfaces as before, so no job has to know what is behind them.

**Who the call is for.** Enrichment is background work: a job runs long after the request
that queued it, with no session to forward. So the suite is called as GAM, on behalf of
the user who owns the asset (`SUITE_APP_SECRET`). The suite credits the spend to the app,
and an app credential can only use the gateway, never change anyone's settings.

**What it is for.** Describing, summarising and tagging are three jobs the suite can route
to three models (`enrich.describe`, `enrich.summarize`, `enrich.autotag`), each falling
back to the assistant's. The spend is attributed to the asset (`gam:asset:<id>`), and
counted by the suite, so none of it is written to this app's own usage table in this mode.

**When it is not there.** Search must not stop. `build_embedder` answers None for a suite
that is down, exactly as for no embedder at all, and keyword search carries on. Jobs that
need the model fail with a sentence saying so, and retry like any other failed job.
"""

from __future__ import annotations

import logging
from contextlib import closing
from typing import Optional, Sequence

import httpx
from gecko_suite_client import (
    SuiteChat,
    SuiteClient,
    SuiteEmbedder,
    SuiteError,
    SuiteUnavailable,
)
from gecko_suite_client import Image as SuiteImage

from app.config import settings
from app.embeddings.base import EmbeddingError
from app.providers.base import Completion, Image, ProviderError, Usage

logger = logging.getLogger(__name__)

# Tests set this; production leaves it None, which is httpx's real network transport.
TRANSPORT: Optional[httpx.BaseTransport] = None

APP = "gam"

# The default job for a caller that does not say: reading text and answering in text.
DEFAULT_TASK = "enrich.summarize"


def enabled() -> bool:
    return settings.ai_backend == "suite"


def client(user_id: str) -> SuiteClient:
    """A connection to the suite as GAM, acting for `user_id`.

    One per operation and closed after it: the credential names the user, so a client
    cannot be shared between users, and a job's call is minutes apart from the next.
    """
    return SuiteClient(
        settings.suite_base_url,
        app=APP,
        app_secret=settings.suite_app_secret,
        on_behalf_of=user_id,
        transport=TRANSPORT,
    )


def sentence(exc: SuiteError) -> str:
    """What to tell the person. The suite's messages are written to be shown, except for
    the case where it is not there to write one."""
    if isinstance(exc, SuiteUnavailable):
        return ("Gecko Suite can't be reached right now. The library still works; "
                "enrichment and semantic search resume when it is back.")
    return exc.message


# ─── generation: describe, summarise, tag ────────────────────────────────────


class SuiteLLM:
    """`LLMProvider` (providers/base.py) over the suite's gateway."""

    def __init__(self, user_id: str, task: str, subject: Optional[str], model: dict):
        self._user_id, self._task, self._subject = user_id, task, subject
        self._model = model

    @property
    def model(self) -> str:
        return self._model.get("model_id", "")

    @property
    def provider_type(self) -> str:
        return self._model.get("vendor", "")

    @property
    def supports_images(self) -> bool:
        return "vision" in self._model.get("capabilities", [])

    @property
    def supports_tools(self) -> bool:
        return "tools" in self._model.get("capabilities", [])

    @property
    def label(self) -> str:
        return self._model.get("label") or self.model

    def complete(self, prompt: str, *, system: Optional[str] = None,
                 images: Sequence[Image] = ()) -> Completion:
        try:
            with closing(client(self._user_id)) as connection:
                conversation = SuiteChat(
                    connection, task=self._task, app=APP, subject_ref=self._subject
                ).conversation(
                    system=[system] if system else [],
                    prompt=prompt,
                    images=[SuiteImage(media_type=i.media_type, data=i.data) for i in images],
                )
                reply = conversation.ask()
        except SuiteError as exc:
            raise ProviderError(sentence(exc)) from exc
        if reply.stop == "refusal":
            raise ProviderError("The model declined this request"
                                + (f" ({reply.refusal_category})." if reply.refusal_category else "."))
        return Completion(
            text=reply.text,
            usage=Usage(input_tokens=reply.input_tokens, output_tokens=reply.output_tokens),
            model=reply.model,
            provider_type=reply.vendor,
        )


def build_llm(user_id: str, task: str = DEFAULT_TASK,
              subject: Optional[str] = None) -> Optional[SuiteLLM]:
    """The model this user's `task` is routed to, or None if nothing is.

    Raises ProviderError when the suite cannot be reached, which is not the same as
    "nothing is set up": a job should say what happened, and a retry is the right answer.
    """
    try:
        with closing(client(user_id)) as connection:
            model = SuiteChat(connection, task=task, app=APP).resolve()
    except SuiteError as exc:
        raise ProviderError(sentence(exc)) from exc
    return SuiteLLM(user_id, task, subject, model) if model else None


# ─── the assistant ───────────────────────────────────────────────────────────

# The job the assistant's conversation names, so it can be given a model of its own.
ASSISTANT_TASK = "chat.assistant"


def assistant_chat(connection: SuiteClient, subject: Optional[str]) -> SuiteChat:
    return SuiteChat(connection, task=ASSISTANT_TASK, app=APP, subject_ref=subject)


# ─── embeddings ──────────────────────────────────────────────────────────────


class SuiteEmbedding:
    """`Embedder` (embeddings/base.py) over the suite's gateway."""

    def __init__(self, user_id: str, model: str, dimensions: int):
        self._user_id = user_id
        self._model = model
        self._dimensions = dimensions

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            with closing(client(self._user_id)) as connection:
                vectors = SuiteEmbedder(
                    connection, app=APP, dimensions=settings.suite_embedding_dimensions
                ).embed(texts)
        except SuiteError as exc:
            raise EmbeddingError(sentence(exc)) from exc
        return vectors


def build_embedding(user_id: str) -> Optional[SuiteEmbedding]:
    """This user's embedder, or None when none is routed, or the suite is not there.

    None for both on purpose: search treats it as "no semantic half" and answers with
    keywords, which is the right behaviour when the suite is down. (A person who sees no
    semantic results can tell from Settings, which links to the suite either way.)
    """
    try:
        with closing(client(user_id)) as connection:
            embedder = SuiteEmbedder(connection, app=APP,
                                     dimensions=settings.suite_embedding_dimensions)
            if embedder.resolve() is None:
                return None
            model, dimensions = embedder.model, embedder.dimensions
    except SuiteError as exc:
        logger.warning("Embeddings unavailable from Gecko Suite: %s", sentence(exc))
        return None
    return SuiteEmbedding(user_id, model, dimensions)
