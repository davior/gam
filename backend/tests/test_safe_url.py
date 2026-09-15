"""The SSRF guard.

Every check here is about a URL a *user* chose. The threat is not a malformed address —
it is a well-formed one that resolves somewhere the browser could never reach but the
container can: a metadata endpoint, an internal admin port, a neighbouring service.
"""

import socket

import pytest
from fastapi import HTTPException

from app.safe_url import require_safe_external_url


def _code(exc_info) -> str:
    return exc_info.value.detail["code"]


# ─── scheme ──────────────────────────────────────────────────────────────────


def test_https_is_required():
    with pytest.raises(HTTPException) as exc_info:
        require_safe_external_url("http://api.example.com")
    assert exc_info.value.status_code == 400
    assert _code(exc_info) == "invalid_url"


def test_a_non_http_scheme_is_refused():
    """file:// and gopher:// are the classic ways to turn a fetch into a local read."""
    with pytest.raises(HTTPException) as exc_info:
        require_safe_external_url("file:///etc/passwd")
    assert _code(exc_info) == "invalid_url"


def test_something_that_is_not_a_url_at_all():
    with pytest.raises(HTTPException) as exc_info:
        require_safe_external_url("https://")
    assert _code(exc_info) == "invalid_url"


# ─── literal addresses ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/v1",
        "https://10.0.0.5:8080",
        "https://192.168.1.10",
        "https://172.16.4.4",
        # The cloud metadata endpoint, which is the single most valuable target here.
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/v1",
        "https://[fd00::1]/v1",
    ],
)
def test_private_and_loopback_literals_are_refused(url):
    with pytest.raises(HTTPException) as exc_info:
        require_safe_external_url(url)
    assert _code(exc_info) == "ssrf_blocked"


def test_a_public_literal_is_allowed():
    require_safe_external_url("https://8.8.8.8/v1")


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "api.localhost"])
def test_localhost_by_name_is_refused(host):
    with pytest.raises(HTTPException) as exc_info:
        require_safe_external_url(f"https://{host}/v1")
    assert _code(exc_info) == "ssrf_blocked"


# ─── resolution ──────────────────────────────────────────────────────────────


def _resolving_to(address: str, family=socket.AF_INET):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(family, socket.SOCK_STREAM, 6, "", (address, 443))]

    return fake_getaddrinfo


def test_a_public_name_pointing_at_loopback_is_refused(monkeypatch):
    """The case gecko-notes' version misses.

    It checks literal addresses only, so a name the attacker controls — pointed at
    127.0.0.1 by their own DNS — walks straight through it. That is the whole attack,
    not an edge case.
    """
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to("127.0.0.1"))

    with pytest.raises(HTTPException) as exc_info:
        require_safe_external_url("https://totally-fine.example.com/v1")
    assert _code(exc_info) == "ssrf_blocked"


def test_a_name_pointing_at_a_private_range_is_refused(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to("10.1.2.3"))

    with pytest.raises(HTTPException) as exc_info:
        require_safe_external_url("https://gateway.example.com")
    assert _code(exc_info) == "ssrf_blocked"


def test_a_name_resolving_to_a_public_address_is_allowed(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to("93.184.216.34"))

    require_safe_external_url("https://api.example.com/v1")


def test_every_resolved_address_is_checked_not_just_the_first(monkeypatch):
    """A name can answer with several addresses, and one private answer is enough."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(HTTPException) as exc_info:
        require_safe_external_url("https://split.example.com")
    assert _code(exc_info) == "ssrf_blocked"


def test_a_resolution_failure_does_not_block(monkeypatch):
    """Deliberate, and the reason this guard is defence in depth rather than the only line.

    Refusing to *save* a setting because a lookup failed is a worse failure than letting
    the eventual request fail on its own merits — the resolver may be temporarily
    unreachable, or the name may only resolve from where the request is finally made.
    """

    def fake_getaddrinfo(host, port, *args, **kwargs):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    require_safe_external_url("https://not-yet-registered.example.com")
