"""One POST to a provider, with the retries and the timeout split.

This is the piece GAM has been missing everywhere, not just here: `enrichment/deepgram.py`
and both embedders call bare `httpx.post` with no retry at all, so a 429 during a
backfill is a hard failure on the first rate-limit rather than a pause. Ported from
gecko-notes' `_post_upstream`, made synchronous — every upstream call in GAM runs on a
job worker thread, so blocking one is the intended behaviour and an event loop is not
involved.

Two decisions carried over from there, both worth keeping:

- **Only 429 and 503 are retried.** They mean "ask again", and nothing was produced or
  billed on the attempt that returned them, so a fresh request is safe. A 400 or a 401
  will fail identically forever and retrying it just delays the error.
- **The timeout is split.** A blocking completion returns no bytes until the whole
  answer is generated, so the read window has to cover generation — but connect, write
  and pool stay short, so a genuinely dead endpoint fails in seconds instead of hanging
  for the entire read budget.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Mapping, Optional

import httpx

from app.providers.base import ProviderError

logger = logging.getLogger(__name__)

RETRY_STATUS_CODES = frozenset({429, 503})
MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 0.5

# A provider that asks for a longer wait than this is telling us to come back later, not
# to hold a worker thread open. Past the cap the run fails and the user can start it
# again — one job stuck for an hour would block everything queued behind it.
MAX_RETRY_AFTER = 30.0


def _retry_after(response: httpx.Response, fallback: float) -> float:
    """Honour a `Retry-After` header when the provider sends one.

    gecko-notes backs off blindly. A provider that has told us exactly how long to wait
    is better information than an exponential guess — but it is also attacker-adjacent
    input for a `custom` endpoint, so it is clamped rather than trusted.
    """
    raw = response.headers.get("retry-after")
    if not raw:
        return fallback
    try:
        seconds = float(raw.strip())
    except ValueError:
        # The header may also be an HTTP date. Parsing one to shave a fraction off a
        # backoff is not worth the timezone handling it needs to be correct.
        return fallback
    if seconds <= 0:
        return fallback
    return min(seconds, MAX_RETRY_AFTER)


def post_json(
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    json_body: Mapping[str, Any],
    timeout: float,
    label: str,
    sleep=time.sleep,
) -> httpx.Response:
    """POST and return the response, retrying only what is worth retrying.

    Connection-level failures become ProviderError with a sentence naming the provider;
    an HTTP error status is returned as-is, because only the caller knows how to read
    its body. `sleep` is injected so tests do not spend the backoff.
    """
    client_timeout = httpx.Timeout(
        timeout,
        connect=min(timeout, 10.0),
        write=min(timeout, 30.0),
        pool=min(timeout, 10.0),
    )

    try:
        with httpx.Client(timeout=client_timeout) as client:
            for attempt in range(MAX_ATTEMPTS):
                response = client.post(url, headers=dict(headers or {}), json=dict(json_body))
                if response.status_code not in RETRY_STATUS_CODES:
                    return response
                if attempt == MAX_ATTEMPTS - 1:
                    return response

                delay = _retry_after(response, RETRY_BASE_DELAY * (2**attempt))
                logger.info(
                    "%s returned %d; retrying in %.1fs (attempt %d of %d)",
                    label,
                    response.status_code,
                    delay,
                    attempt + 1,
                    MAX_ATTEMPTS,
                )
                sleep(delay)
            return response
    except httpx.TimeoutException as exc:
        raise ProviderError(f"{label} did not respond in time") from exc
    except httpx.RequestError as exc:
        # The exception type, not its text: a RequestError's message can carry the full
        # URL, and a `custom` provider's URL is user-supplied.
        raise ProviderError(f"Could not reach {label} ({type(exc).__name__})") from exc


def get_json(url: str, *, timeout: float, label: str) -> httpx.Response:
    """The read-only counterpart, for probing whether a local daemon is up."""
    try:
        return httpx.get(url, timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)))
    except httpx.TimeoutException as exc:
        raise ProviderError(f"{label} did not respond in time") from exc
    except httpx.RequestError as exc:
        raise ProviderError(f"Could not reach {label} ({type(exc).__name__})") from exc


def error_detail(response: httpx.Response) -> str:
    """A sentence from an error response, whatever shape it arrived in.

    Every provider nests its message somewhere different and some send HTML, so this
    tries the known shapes and falls back to the status code rather than pasting a page
    of markup into a job's error field.
    """
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"

    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(error, str):
            return error
        for key in ("message", "detail", "err_msg"):
            if isinstance(body.get(key), str):
                return body[key]

    return f"HTTP {response.status_code}"
