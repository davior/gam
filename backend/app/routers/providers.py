"""CRUD over the generative LLM providers a user has configured.

Its own router rather than a third section of `settings.py`: this is a collection of
rows, not a settings blob like `/api/settings/speech`, and `docs/m6-ai-enrichment.md`
notes that gecko-notes' equivalent file reached 3,284 lines by absorbing everything
adjacent. GAM's settings router is 200-odd lines and should stay that shape.

Nothing consumes these rows yet — the clients that speak to the endpoints are step 2 of
the milestone. `POST /test` is here because of that, not in spite of it: without it there
would be no way to find out whether a key and model are right until a job existed to fail.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session, select

from app.auth import CurrentUser, decrypt_api_key, encrypt_api_key
from app.database import get_session
from app.models.provider import AIProvider
from app.providers import (
    CHECKED_BASE_URL_TYPES,
    PROVIDER_ANTHROPIC,
    PROVIDER_OLLAMA,
    PROVIDER_TYPES,
)
from app.providers.endpoints import (
    OLLAMA_BASE,
    anthropic_base,
    anthropic_headers,
    openai_compat_base,
)
from app.safe_url import require_safe_external_url
from app.schemas import DataResponse, ListResponse
from app.schemas_providers import (
    AIProviderCreate,
    AIProviderRead,
    AIProviderTest,
    AIProviderUpdate,
    ProviderTestResult,
)
from app.clock import utcnow

logger = logging.getLogger(__name__)

router = APIRouter()

# Short on purpose. This is a reachability check a user is watching a spinner for, not a
# real completion — a provider that cannot answer in ten seconds has failed the question
# being asked.
PROBE_TIMEOUT = httpx.Timeout(10.0)

# The smallest exchange that still proves the credentials, the route and the model name.
PROBE_BODY = {"max_tokens": 10, "messages": [{"role": "user", "content": "Hi"}]}


def _serialise(provider: AIProvider) -> AIProviderRead:
    """The row as the browser sees it: everything except the credential."""
    data = AIProviderRead.model_validate(provider)
    data.api_key_configured = bool(provider.api_key)
    return data


def _require_known_type(provider_type: str) -> None:
    if provider_type not in PROVIDER_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "bad_request",
                "message": f"Unknown provider type: {provider_type}",
            },
        )


def _check_base_url(provider_type: str, base_url: Optional[str]) -> None:
    """SSRF-check a base URL, for the types whose base URL is actually fetched.

    `anthropic` and `deepseek` resolve to fixed managed endpoints and never read the
    stored value, and `ollama` is deliberately allowed to be a private address.
    """
    if base_url and provider_type in CHECKED_BASE_URL_TYPES:
        require_safe_external_url(base_url)


def _owned(session: Session, provider_id: str, user_id: str) -> AIProvider:
    provider = session.get(AIProvider, provider_id)
    if not provider or provider.user_id != user_id:
        # 404 rather than 403 for someone else's row: whether a provider id exists is
        # not a question this API answers for people who do not own it.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "No such provider"},
        )
    return provider


def _deactivate_others(session: Session, user_id: str, keep: Optional[str]) -> None:
    """At most one active provider per user."""
    others = session.exec(select(AIProvider).where(AIProvider.user_id == user_id)).all()
    for other in others:
        if other.id != keep and other.is_active:
            other.is_active = False
            other.updated_at = utcnow()
            session.add(other)


@router.get("", response_model=ListResponse[AIProviderRead])
def list_providers(
    user: CurrentUser, session: Session = Depends(get_session)
) -> ListResponse[AIProviderRead]:
    providers = session.exec(
        select(AIProvider)
        .where(AIProvider.user_id == user.id)
        .order_by(AIProvider.created_at)
    ).all()
    return ListResponse(
        data=[_serialise(p) for p in providers],
        total=len(providers),
        limit=len(providers),
        offset=0,
    )


@router.post("", response_model=DataResponse[AIProviderRead], status_code=201)
def create_provider(
    payload: AIProviderCreate,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[AIProviderRead]:
    _require_known_type(payload.provider_type)
    _check_base_url(payload.provider_type, payload.base_url)

    provider = AIProvider(
        user_id=user.id,
        name=payload.name,
        provider_type=payload.provider_type,
        api_key=encrypt_api_key(payload.api_key) if payload.api_key else "",
        base_url=payload.base_url or None,
        model=payload.model,
        max_tokens=payload.max_tokens,
        supports_images=payload.supports_images,
        use_anthropic_api=payload.use_anthropic_api,
        extra_params=json.dumps(payload.extra_params) if payload.extra_params else None,
        enabled=payload.enabled,
        is_active=payload.is_active,
    )

    if payload.is_active:
        _deactivate_others(session, user.id, keep=None)

    session.add(provider)
    session.commit()
    session.refresh(provider)
    return DataResponse(data=_serialise(provider))


@router.put("/{provider_id}", response_model=DataResponse[AIProviderRead])
def update_provider(
    provider_id: str,
    payload: AIProviderUpdate,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[AIProviderRead]:
    provider = _owned(session, provider_id, user.id)

    if payload.provider_type is not None:
        _require_known_type(payload.provider_type)

    # Validated before anything is stored, and against the type this request results in
    # rather than the one already on the row — otherwise changing only the URL, or
    # changing the type and the URL together, skips the check that the stored URL is
    # what a later request will be sent to.
    effective_type = payload.provider_type or provider.provider_type
    _check_base_url(effective_type, payload.base_url)

    for field in (
        "name",
        "provider_type",
        "base_url",
        "model",
        "max_tokens",
        "supports_images",
        "use_anthropic_api",
        "enabled",
        "is_active",
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(provider, field, value)

    # A dict on the wire, JSON text in the column; {} clears it.
    if payload.extra_params is not None:
        provider.extra_params = (
            json.dumps(payload.extra_params) if payload.extra_params else None
        )

    # None leaves the stored key alone — the browser never had it to send back. "" is
    # the only way to remove one, so it has to be distinguishable from "unchanged".
    if payload.api_key is not None:
        provider.api_key = encrypt_api_key(payload.api_key) if payload.api_key else ""

    if payload.is_active:
        _deactivate_others(session, user.id, keep=provider.id)

    provider.updated_at = utcnow()
    session.add(provider)
    session.commit()
    session.refresh(provider)
    return DataResponse(data=_serialise(provider))


# response_model=None alongside the `-> None` annotation: FastAPI infers a response
# model from the return type, and NoneType is a type like any other, so it would try to
# give a 204 a body and refuse at import time. Same as app/routers/tags.py.
@router.delete(
    "/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
def delete_provider(
    provider_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> None:
    provider = _owned(session, provider_id, user.id)
    session.delete(provider)
    session.commit()


@router.post("/{provider_id}/activate", response_model=DataResponse[AIProviderRead])
def activate_provider(
    provider_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[AIProviderRead]:
    provider = _owned(session, provider_id, user.id)

    _deactivate_others(session, user.id, keep=provider.id)
    provider.is_active = True
    provider.updated_at = utcnow()
    session.add(provider)
    session.commit()
    session.refresh(provider)
    return DataResponse(data=_serialise(provider))


def _probe_result(status_code: int) -> ProviderTestResult:
    """Read an HTTP status as an answer about reachability, not about the probe body.

    400 counts as success: the endpoint answered, which means the URL and the credential
    were both accepted — it is the ten-token message it did not like, and that is not the
    question. 401/403/404 are the ones that mean the configuration is wrong.
    """
    if status_code in (200, 400):
        return ProviderTestResult(success=True, message="Connected")
    if status_code in (401, 403):
        return ProviderTestResult(success=False, message="The API key was rejected")
    if status_code == 404:
        return ProviderTestResult(
            success=False, message="No such model, or the wrong address"
        )
    if status_code == 429:
        return ProviderTestResult(
            success=False, message="Rate limited or out of quota"
        )
    return ProviderTestResult(success=False, message=f"The provider returned HTTP {status_code}")


@router.post("/test", response_model=DataResponse[ProviderTestResult])
def test_provider(
    payload: AIProviderTest,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[ProviderTestResult]:
    """Ask the provider a ten-token question and report whether it answered.

    Synchronous `httpx`, like every other upstream call in this app (`enrichment/
    deepgram.py`, `embeddings/openai.py`): FastAPI runs a sync endpoint in a threadpool,
    so this blocks a worker thread and not the event loop.
    """
    _require_known_type(payload.provider_type)

    api_key = payload.api_key
    base_url = payload.base_url
    if payload.provider_id:
        saved = _owned(session, payload.provider_id, user.id)
        # Re-testing a saved provider must not require the key again: the browser was
        # never given it, so it has nothing to send.
        api_key = decrypt_api_key(saved.api_key)
        base_url = base_url or saved.base_url

    # A provider that speaks the Anthropic protocol is tested against THAT endpoint
    # whatever its type — otherwise a DeepSeek provider on api.deepseek.com/anthropic
    # would be tested against the OpenAI-compatible endpoint it no longer uses, and
    # report a result for the wrong URL entirely.
    speaks_anthropic = (
        payload.provider_type == PROVIDER_ANTHROPIC or payload.use_anthropic_api
    )

    probe = AIProvider(
        user_id=user.id,
        name="probe",
        provider_type=payload.provider_type,
        base_url=base_url,
        model=payload.model,
        use_anthropic_api=payload.use_anthropic_api,
    )

    try:
        if speaks_anthropic:
            target = anthropic_base(probe)
            # gecko-notes checks the OpenAI-compatible branch and not this one, which
            # leaves a `custom` provider's payload-supplied base_url going straight into
            # an outbound POST. Check whatever is about to be fetched, on both paths.
            _check_base_url(payload.provider_type, base_url)
            response = httpx.post(
                f"{target}/v1/messages",
                headers=anthropic_headers(api_key, payload.provider_type),
                json={"model": payload.model, **PROBE_BODY},
                timeout=PROBE_TIMEOUT,
            )
        elif payload.provider_type == PROVIDER_OLLAMA:
            # The one base URL allowed to be private, and the one provider with no
            # credential to check — so "is it running" is the whole question.
            target = (base_url or OLLAMA_BASE).rstrip("/")
            response = httpx.get(f"{target}/api/tags", timeout=PROBE_TIMEOUT)
            return DataResponse(
                data=ProviderTestResult(success=True, message="Ollama is reachable")
                if response.status_code == 200
                else ProviderTestResult(
                    success=False, message=f"Ollama returned HTTP {response.status_code}"
                )
            )
        else:
            target = openai_compat_base(payload.provider_type, base_url)
            _check_base_url(payload.provider_type, base_url)
            response = httpx.post(
                f"{target}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "content-type": "application/json",
                },
                json={"model": payload.model, **PROBE_BODY},
                timeout=PROBE_TIMEOUT,
            )
    except HTTPException:
        raise
    except httpx.TimeoutException:
        return DataResponse(
            data=ProviderTestResult(success=False, message="The provider did not respond in time")
        )
    except httpx.HTTPError as exc:
        # The URL and the exception text can both carry the key; log neither.
        logger.warning("Provider probe failed: %s", type(exc).__name__)
        return DataResponse(
            data=ProviderTestResult(success=False, message="Could not reach that address")
        )

    return DataResponse(data=_probe_result(response.status_code))
