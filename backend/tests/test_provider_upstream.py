"""The retry, backoff and timeout translation.

GAM had none of this anywhere before: `enrichment/deepgram.py` and both embedders call
a bare `httpx.post`, so the first 429 of a backfill is a hard failure. These are the
cases that behaviour turns on.
"""

import httpx
import pytest

from app.providers import _upstream
from app.providers.base import ProviderError
from app.providers.params import PROTECTED_KEYS, parse_extra_params


class FakeClient:
    """Stands in for httpx.Client, handing back a scripted sequence of responses."""

    def __init__(self, responses, recorder):
        self._responses = list(responses)
        self._recorder = recorder

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        self._recorder.append({"url": url, "headers": headers, "json": json})
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(name="transport")
def transport_fixture(monkeypatch):
    """Script the responses, record the requests, and never actually sleep."""
    state = {"requests": [], "slept": []}

    def install(*responses):
        monkeypatch.setattr(
            httpx, "Client", lambda **kwargs: FakeClient(responses, state["requests"])
        )
        return state

    state["install"] = install
    return state


def _post(state, **overrides):
    kwargs = {
        "headers": {"x-api-key": "secret"},
        "json_body": {"model": "m"},
        "timeout": 30.0,
        "label": "Test provider",
        "sleep": state["slept"].append,
    }
    kwargs.update(overrides)
    return _upstream.post_json("https://api.example.com/v1/messages", **kwargs)


# ─── what is retried ─────────────────────────────────────────────────────────


def test_a_rate_limit_is_retried_and_can_succeed(transport):
    state = transport["install"](
        httpx.Response(429, json={}), httpx.Response(200, json={"ok": True})
    )

    response = _post(state)

    assert response.status_code == 200
    assert len(state["requests"]) == 2
    assert state["slept"] == [0.5]


def test_backoff_doubles(transport):
    state = transport["install"](
        httpx.Response(503, json={}),
        httpx.Response(503, json={}),
        httpx.Response(200, json={}),
    )

    _post(state)

    assert state["slept"] == [0.5, 1.0]


def test_it_gives_up_after_three_attempts_and_hands_back_the_failure(transport):
    state = transport["install"](*[httpx.Response(429, json={}) for _ in range(3)])

    response = _post(state)

    assert response.status_code == 429
    assert len(state["requests"]) == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 500])
def test_everything_else_is_returned_immediately(transport, status):
    """A 400 will fail identically forever; retrying it only delays the error."""
    state = transport["install"](httpx.Response(status, json={}))

    response = _post(state)

    assert response.status_code == status
    assert len(state["requests"]) == 1
    assert state["slept"] == []


# ─── Retry-After ─────────────────────────────────────────────────────────────


def test_a_retry_after_header_is_honoured(transport):
    """Better information than an exponential guess — the provider said how long."""
    state = transport["install"](
        httpx.Response(429, headers={"retry-after": "4"}), httpx.Response(200, json={})
    )

    _post(state)

    assert state["slept"] == [4.0]


def test_an_absurd_retry_after_is_clamped(transport):
    """It is attacker-adjacent input for a custom endpoint, and a worker thread held
    open for an hour blocks everything queued behind it."""
    state = transport["install"](
        httpx.Response(429, headers={"retry-after": "86400"}), httpx.Response(200, json={})
    )

    _post(state)

    assert state["slept"] == [_upstream.MAX_RETRY_AFTER]


@pytest.mark.parametrize("value", ["", "soon", "-1", "0", "Wed, 21 Oct 2026 07:28:00 GMT"])
def test_an_unusable_retry_after_falls_back_to_the_backoff(transport, value):
    state = transport["install"](
        httpx.Response(429, headers={"retry-after": value}), httpx.Response(200, json={})
    )

    _post(state)

    assert state["slept"] == [0.5]


# ─── connection failures ─────────────────────────────────────────────────────


def test_a_timeout_becomes_a_readable_error(transport):
    state = transport["install"](httpx.ReadTimeout("too slow"))

    with pytest.raises(ProviderError) as exc_info:
        _post(state)
    assert "Test provider" in str(exc_info.value)
    assert "time" in str(exc_info.value)


def test_an_unreachable_host_does_not_leak_the_url(transport):
    """A RequestError's own text carries the full URL, and a custom provider's URL is
    user-supplied — so the type is reported and the message is not."""
    state = transport["install"](
        httpx.ConnectError("failed to connect to https://internal.example.com/secret")
    )

    with pytest.raises(ProviderError) as exc_info:
        _post(state)
    message = str(exc_info.value)
    assert "ConnectError" in message
    assert "internal.example.com" not in message


# ─── reading an error body ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"error": {"message": "Invalid API key"}}, "Invalid API key"),
        ({"error": "model not found"}, "model not found"),
        ({"message": "quota exceeded"}, "quota exceeded"),
        ({"detail": "nope"}, "nope"),
    ],
)
def test_the_message_is_found_wherever_the_provider_put_it(payload, expected):
    assert _upstream.error_detail(httpx.Response(400, json=payload)) == expected


def test_a_page_of_html_becomes_the_status_code():
    """Otherwise a gateway's error page ends up pasted into a job's error field."""
    response = httpx.Response(502, text="<html><body>Bad Gateway</body></html>")

    assert _upstream.error_detail(response) == "HTTP 502"


# ─── extra_params ────────────────────────────────────────────────────────────


def test_extra_params_pass_tuning_through():
    assert parse_extra_params('{"temperature": 0, "top_p": 0.9}') == {
        "temperature": 0,
        "top_p": 0.9,
    }


@pytest.mark.parametrize("key", sorted(PROTECTED_KEYS))
def test_a_structural_key_is_stripped(key):
    """A blob that sets `messages` does not tune a request, it replaces one."""
    assert parse_extra_params('{"%s": "hijacked", "temperature": 1}' % key) == {
        "temperature": 1
    }


@pytest.mark.parametrize("raw", [None, "", "not json", "[1, 2]", '"a string"', "null"])
def test_anything_unusable_reads_as_none_set(raw):
    """Costing the user a tuning parameter beats failing the whole enrichment run."""
    assert parse_extra_params(raw) == {}
