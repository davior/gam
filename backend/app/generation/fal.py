"""fal.ai's queue API, spoken over raw httpx.

Raw rather than through fal's own clients, for gecko-notes' reason, which holds: neither
`fal-client` nor `@fal-ai/client` exposes response headers, and `x-fal-billable-units`
on the result is the whole of exact costing. The four calls are a hundred lines.

The queue for every kind, never the synchronous `fal.run`: a video takes minutes, a
dropped connection there loses a result fal still bills for, and nothing about a
synchronous call survives a restart. Submit returns a request id and three URLs, which
the job persists before it polls — see `run.py`.

Every failure is a GenerationError worded for the activity feed. What fal says is kept
where it helps (a 422 names the field it wanted) and replaced where it does not (a 401
is a key problem, whatever its body says).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.generation.errors import FalUnavailable, GenerationError
from app.providers import _upstream
from app.providers.base import ProviderError

logger = logging.getLogger(__name__)

LABEL = "fal.ai"

# Submit carries the base images inline — up to ten data URIs of up to 8 MB each — so it
# gets a long budget. The others return a small JSON document.
SUBMIT_TIMEOUT = 120.0
CALL_TIMEOUT = 30.0
CANCEL_TIMEOUT = 10.0

STATUS_IN_QUEUE = "IN_QUEUE"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_COMPLETED = "COMPLETED"

# Namespaces whose app id is three segments rather than two (fal's own clients encode
# the same exception).
_THREE_SEGMENT_NAMESPACES = frozenset({"workflows", "comfy"})


@dataclass(frozen=True)
class QueuedRequest:
    """What submit returned: enough to poll, fetch and cancel from another process."""

    request_id: str
    status_url: str
    response_url: str
    cancel_url: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> Optional["QueuedRequest"]:
        if not isinstance(data, dict):
            return None
        try:
            return cls(**{name: str(data[name]) for name in cls.__dataclass_fields__})
        except KeyError:
            return None


@dataclass(frozen=True)
class QueueStatus:
    status: str
    queue_position: Optional[int] = None
    # Set alongside COMPLETED when the run failed: fal reports a failure as completed.
    error: Optional[str] = None


@dataclass(frozen=True)
class QueueResult:
    body: dict
    # A decimal string in the endpoint's priced unit, when fal sends it.
    billable_units: Optional[str]
    request_id: Optional[str]


def auth_headers(api_key: str) -> dict[str, str]:
    """fal's scheme is `Key`, not `Bearer` — sent literally, `id:secret` pairs included."""
    return {"Authorization": f"Key {api_key}"}


# ─── the four calls ──────────────────────────────────────────────────────────


def submit(endpoint_id: str, body: Mapping[str, Any], *, api_key: str) -> QueuedRequest:
    """Queue one request. Retries 429/503 only — nothing is billed on those."""
    url = f"{_queue_base()}/{endpoint_id}"
    response = _upstream.post_json(
        url,
        headers=auth_headers(api_key),
        json_body=body,
        timeout=SUBMIT_TIMEOUT,
        label=LABEL,
    )
    if response.status_code >= 400:
        raise error_for(response)

    data = _json(response)
    request_id = data.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise GenerationError("fal.ai accepted the request but did not say what it was called")

    # fal's own URLs are preferred — the path rule below has exceptions fal may add to —
    # but only while they point back at fal. The key goes to whatever is stored here on
    # every poll, so a URL elsewhere is replaced rather than trusted.
    built = f"{_queue_base()}/{_app_id(endpoint_id)}/requests/{request_id}"
    return QueuedRequest(
        request_id=request_id,
        status_url=_same_origin_or(data.get("status_url"), f"{built}/status"),
        response_url=_same_origin_or(data.get("response_url"), built),
        cancel_url=_same_origin_or(data.get("cancel_url"), f"{built}/cancel"),
    )


def status(request: QueuedRequest, *, api_key: str) -> QueueStatus:
    response = _upstream.request(
        "GET",
        request.status_url,
        headers=auth_headers(api_key),
        params={"logs": "0"},
        timeout=CALL_TIMEOUT,
        label=LABEL,
    )
    if response.status_code >= 400:
        raise error_for(response)

    data = _json(response)
    position = data.get("queue_position")
    if isinstance(position, bool) or not isinstance(position, int):
        position = None
    error = data.get("error")
    return QueueStatus(
        status=str(data.get("status") or ""),
        queue_position=position,
        error=str(error) if error else None,
    )


def result(request: QueuedRequest, *, api_key: str) -> QueueResult:
    response = _upstream.request(
        "GET",
        request.response_url,
        headers=auth_headers(api_key),
        timeout=CALL_TIMEOUT,
        label=LABEL,
    )
    if response.status_code >= 400:
        if response.status_code >= 500 and "x-fal-request-id" in response.headers:
            # The model's own failure, not the gateway's: fal stamps its request id on
            # what its servers answer. fal's Python client does not retry these either.
            # Left as `FalUnavailable`, the poll loop would fetch it again every few
            # seconds until the deadline, then report a generation that did finish as
            # one that "did not finish".
            raise GenerationError(
                f"fal.ai could not generate this: {_upstream.error_detail(response)}"
            )
        raise error_for(response)
    return QueueResult(
        body=_json(response),
        billable_units=response.headers.get("x-fal-billable-units"),
        request_id=response.headers.get("x-fal-request-id"),
    )


def cancel(request: QueuedRequest, *, api_key: str) -> bool:
    """Ask fal to stop. Best effort, never raises; returns whether fal accepted it.

    Sent on a user's cancel and on GAM's own timeout, because a request abandoned here
    keeps running — and billing — there. 202 means accepted, not stopped; a running job
    may finish anyway, and nothing here waits to find out.
    """
    try:
        response = _upstream.request(
            "PUT",
            request.cancel_url,
            headers=auth_headers(api_key),
            timeout=CANCEL_TIMEOUT,
            label=LABEL,
        )
    except ProviderError:
        logger.warning("Could not ask fal.ai to cancel request %s", request.request_id)
        return False
    if response.status_code >= 400:
        logger.info(
            "fal.ai declined to cancel %s: HTTP %d", request.request_id, response.status_code
        )
        return False
    return True


# ─── errors ──────────────────────────────────────────────────────────────────


def error_for(response: httpx.Response) -> GenerationError:
    """A fal error response as a sentence somebody can act on."""
    code = response.status_code
    detail = _upstream.error_detail(response)

    if code in (401, 403):
        # An exhausted balance arrives as a 403 too, as far as integrators report — and
        # "check your key" would send somebody to the wrong page. fal's own words are
        # the better answer there.
        if "balance" in detail.lower():
            return GenerationError(f"fal.ai refused the request: {detail}")
        return GenerationError("fal.ai rejected the API key — check it in Settings")

    if code == 422:
        return GenerationError(
            f"fal.ai refused the request: {_validation_detail(response, detail)}"
        )

    if code == 429 or code >= 500:
        return FalUnavailable(f"fal.ai is not available right now ({detail})")

    return GenerationError(f"fal.ai returned an error: {detail}")


def _validation_detail(response: httpx.Response, fallback: str) -> str:
    """fal's 422 body, `{"detail": [{"loc", "msg"}]}`, as `field: message`.

    `_upstream.error_detail` reads only a string `detail`, so a list falls back to
    "HTTP 422" there — which is exactly the case where fal says which field it wanted.
    `body` is dropped from the location: every field in a request is in the body.
    """
    try:
        body = response.json()
    except ValueError:
        return fallback
    items = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return fallback

    messages = []
    for item in items:
        if not isinstance(item, dict):
            continue
        location = [str(part) for part in item.get("loc") or [] if part != "body"]
        message = str(item.get("msg") or "invalid")
        messages.append(f"{'.'.join(location)}: {message}" if location else message)
    return "; ".join(messages) or fallback


# ─── helpers ─────────────────────────────────────────────────────────────────


def _queue_base() -> str:
    return settings.fal_queue_base_url.rstrip("/")


def _app_id(endpoint_id: str) -> str:
    """The part of an endpoint id the queue's request URLs use.

    Only `owner/alias`: `fal-ai/kling-video/v2.5-turbo/pro/image-to-video` polls at
    `fal-ai/kling-video/requests/{id}/status`. Both of fal's clients do this.
    """
    parts = endpoint_id.split("/")
    keep = 3 if parts[0] in _THREE_SEGMENT_NAMESPACES else 2
    return "/".join(parts[:keep])


def _same_origin_or(url: Any, fallback: str) -> str:
    if not isinstance(url, str) or not url:
        return fallback
    given, expected = urlsplit(url), urlsplit(_queue_base())
    if (given.scheme, given.netloc) != (expected.scheme, expected.netloc):
        logger.warning("fal.ai returned a queue URL on another host; using the documented one")
        return fallback
    return url


def _json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError as exc:
        raise GenerationError("fal.ai sent back something that was not JSON") from exc
    if not isinstance(data, dict):
        raise GenerationError("fal.ai sent back something this app cannot read")
    return data
