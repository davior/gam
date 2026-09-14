"""Verifying the session gecko-notes issued, and encrypting secrets at rest.

GAM issues no tokens and stores no passwords. Gecko Notes is the identity provider for
the suite: it owns registration, password reset and 2FA, and signs an HS256 JWT with a
secret both apps share. GAM's whole job here is to verify that signature and learn who
is calling.

Two transports, in priority order:

1. `Authorization: Bearer <jwt>` — what GAM's own frontend sends, having obtained the
   token from Notes' `/api/auth/session`.
2. The `gecko_session` cookie Notes sets on the parent domain (see GN-1 in
   docs/gecko-notes-integration.md) — what makes a browser already signed in to Notes
   arrive here signed in too.

The header wins when both are present, which keeps an explicit token from being
silently overridden by whatever session the browser happens to be carrying.

Unlike gecko-notes this is a FastAPI dependency, not an HTTP middleware writing to
`request.state`. That repository pays for the middleware approach with a five-line
`_get_user_id` helper copy-pasted into fourteen routers; a dependency gives the same
result once, and typed.
"""

import base64
import hashlib
import hmac
import logging
import time
from typing import Annotated, Optional
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)


class UserCtx(BaseModel):
    """Who is calling, as far as the token says.

    Deliberately thin, because gecko-notes' token is thin — `{sub, username, exp}`,
    with no `is_admin`, `iss` or `aud`. Anything richer has to be read from the local
    shadow user row or fetched from Notes, never inferred from the token.
    """

    id: str
    username: str = ""


# ─── tokens ──────────────────────────────────────────────────────────────────


def decode_token(token: str) -> dict:
    """Verify and decode, or raise JWTError. Signature and expiry are both checked."""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "unauthorized", "message": message},
    )


def _session_not_accepted() -> HTTPException:
    """The caller *is* signed in to Notes, and this app cannot verify it.

    Distinct from `unauthorized` because the remedy is distinct, and because the
    difference is invisible from the browser: both render as "please sign in", and in
    this state signing in again returns the user to exactly the same screen. A cookie
    that carries a well-formed token whose signature does not verify means the two apps
    disagree about JWT_SECRET_KEY — an operator's problem that no amount of clicking
    fixes.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "session_not_accepted",
            "message": (
                "You are signed in to Gecko Notes, but this app could not verify that "
                "session. Its JWT_SECRET_KEY probably does not match Notes'."
            ),
        },
    )


# ─── CSRF, for the cookie path only ──────────────────────────────────────────
#
# A bearer token cannot be attached by a cross-site form, an <img>, or a fetch the
# browser makes on another site's behalf — so the header path needs no protection. The
# cookie is an ambient credential and does need it.
#
# The case that makes this urgent is upload: a cross-origin POST carrying
# multipart/form-data is a *simple* request, so the browser sends it with no preflight
# to block. CORS would stop the attacker reading the response; it would not stop the
# write. Gecko Notes added this guard with the cookie; GAM accepts the same cookie and
# must do the same.

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _host_of(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _origin_allowed_for_cookie(request: Request) -> bool:
    """Whether a cookie-authenticated request may proceed.

    Fails closed on a missing Origin: a browser always sends one (or a Referer) on a
    state-changing request, so its absence means this did not come from a browser doing
    what a browser does.
    """
    if request.method.upper() in _SAFE_METHODS:
        return True

    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return False

    origin_host = _host_of(origin)
    if not origin_host:
        return False

    # Same-origin is always allowed — it is GAM's own frontend talking to GAM's own
    # API, which is the normal deployment (nginx serves both on one origin). Comparing
    # against the Host header rather than a configured value means this needs no extra
    # setting and cannot drift from where the app is actually served.
    request_host = (request.headers.get("host") or "").lower()
    if request_host and origin_host == request_host:
        return True

    return any(origin_host == _host_of(allowed) for allowed in settings.cors_origins)


def current_user(
    request: Request,
    authorization: Annotated[Optional[str], Header()] = None,
    gecko_session: Annotated[Optional[str], Cookie()] = None,
) -> UserCtx:
    """Resolve the caller, or raise 401.

    FastAPI maps the `gecko_session` parameter to the cookie of that name; it is named
    explicitly rather than read off the request so it shows up in the OpenAPI schema
    and can be overridden in tests.
    """
    token: Optional[str] = None
    # Which transport carried it, because it changes what a failure *means*. A cookie
    # was minted by Notes moments ago and handed over by the browser; if it will not
    # verify, the two apps disagree about the secret. A header token came out of this
    # app's own localStorage and could simply be stale.
    from_cookie = False

    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    elif gecko_session:
        token = gecko_session.strip()
        from_cookie = True
        if not _origin_allowed_for_cookie(request):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "forbidden_origin",
                    "message": "Cookie authentication requires an allowed Origin",
                },
            )

    if not token:
        # Only after both real transports have been tried, so a dev environment still
        # exercises the token path whenever a token is actually present.
        if settings.dev_auth_enabled:
            return UserCtx(id=settings.dev_auth_user, username=settings.dev_auth_user)
        raise _unauthorized("Missing bearer token or session cookie")

    try:
        payload = decode_token(token)
    except ExpiredSignatureError as exc:
        # Routine. Tokens last 30 days and then stop; nothing is misconfigured, and the
        # user fixes it by signing in again. Stays at debug so it cannot drown the
        # case below.
        logger.debug("Rejected an expired token")
        raise _unauthorized("Your session has expired. Sign in again.") from exc
    except JWTError as exc:
        # Not routine, and previously logged at debug — which is why a secret mismatch
        # presented as a silent sign-in loop with nothing in the logs at default level.
        # The commonest cause by far is the one named here, so the message carries the
        # diagnosis rather than making somebody derive it.
        logger.warning(
            "Rejected a token that did not verify (%s). If this is every request, "
            "JWT_SECRET_KEY does not match the value gecko-notes signs with.",
            exc,
        )
        raise (_session_not_accepted() if from_cookie else _unauthorized("Invalid token")) from exc

    subject = payload.get("sub")
    if not subject:
        raise _unauthorized("Token carries no subject")

    return UserCtx(id=subject, username=payload.get("username") or "")


CurrentUser = Annotated[UserCtx, Depends(current_user)]


# ─── secrets at rest ─────────────────────────────────────────────────────────


def _fernet() -> Fernet:
    """A Fernet key derived from the JWT secret.

    Derived rather than configured separately so there is one secret to manage, which
    is also what gecko-notes does — worth knowing that it means rotating the JWT secret
    invalidates every stored provider key as well as every session.
    """
    digest = hashlib.sha256(settings.jwt_secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


_ENCRYPTED_PREFIX = "enc:"


def encrypt_api_key(api_key: str) -> str:
    if not api_key:
        return ""
    return _ENCRYPTED_PREFIX + _fernet().encrypt(api_key.encode()).decode()


def decrypt_api_key(value: str) -> str:
    """Decrypt a stored key, tolerating one written before encryption existed.

    A plaintext value is returned as-is rather than treated as corrupt, so a database
    predating encryption keeps working and can be upgraded in place.
    """
    if not value:
        return ""
    if not value.startswith(_ENCRYPTED_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(_ENCRYPTED_PREFIX):].encode()).decode()
    except InvalidToken:
        logger.warning("Stored key could not be decrypted; treating as unset")
        return ""


# ─── signed media URLs ───────────────────────────────────────────────────────
#
# gecko-notes serves /media/* with no authentication at all, so every upload in every
# private note is readable by anyone holding the UUID URL. GAM does not inherit that.
# A signature works where an Authorization header cannot: a <video> or <img> element
# cannot send one, but it can carry query parameters.


def sign_media_key(key: str, *, ttl_seconds: Optional[int] = None) -> tuple[int, str]:
    """Return `(expiry, signature)` for a storage key."""
    ttl = settings.media_url_ttl_seconds if ttl_seconds is None else ttl_seconds
    expires_at = int(time.time()) + ttl
    return expires_at, _media_signature(key, expires_at)


def _media_signature(key: str, expires_at: int) -> str:
    message = f"{key}|{expires_at}".encode()
    digest = hmac.new(settings.jwt_secret_key.encode(), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def verify_media_signature(key: str, expires_at: int, signature: str) -> bool:
    """Constant-time check of a media URL's signature and expiry."""
    if expires_at < int(time.time()):
        return False
    return hmac.compare_digest(_media_signature(key, expires_at), signature)
