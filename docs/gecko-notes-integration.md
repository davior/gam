# Change spec: Gecko Notes → Gecko Suite SSO & GAM integration

**Target repository:** `davior/gecko-notes`
**Written against:** `main` @ `ff3799f`.

**Status — partially applied.** This said "nothing here has been applied" long after that
stopped being true, which is the kind of stale line that makes a whole document
untrustworthy, so it is now itemised:

| | Applied in gecko-notes? |
|---|---|
| GN-1 parent-domain cookie | Reported landed as `95ed2ca`. Not verified from here — see the caveat below. |
| GN-2 `GET /api/auth/session` | Reported landed, same commit. **GAM does not call it yet** — see below. |
| GN-3 CORS/CSP headroom | Reported landed, same commit. |
| GN-4 asset-reference convention | Not applied. It is an M9 concern. |
| GN-5 | Nothing to do — the explicit not-in-this-phase list. |
| GN-6 unrelated findings | Offered, not required. Unapplied. |
| **GN-7 python-jose CVEs** | **Not applied.** Security, independent of GAM. |
| **GN-8 Python version** | **Not applied.** Housekeeping, do it with GN-7. |

**Caveat on the first three rows.** They are secondhand: recorded in
`docs/plan-of-attack.md` when M2 was built, and an attempt to confirm `95ed2ca` against
the GitHub API from this repository returned 403. GAM cannot see gecko-notes' working
tree, so check there before relying on them. The indirect evidence is decent — GAM's M2
is merged and its cookie path works — but that exercises GN-1, not GN-2 or GN-3.

**GN-2 is applied but unused.** GAM's frontend obtains no bearer token; authentication
rides the `gecko_session` cookie alone. `frontend/nginx.conf`'s CSP already permits the
call, so that `connect-src` entry looks dead and is not.

This document is self-contained. A session working in `gecko-notes` should be able to
execute it without any other context.

---

## Why

Gecko Asset Manager (`gam.geckopico.com`) and later Gecko Video Creator
(`gvc.geckopico.com`) are sibling apps under the same parent domain. They need three
things from Gecko Notes, which remains **the identity provider for the whole suite**:

1. a session the browser will send to a *different subdomain* (SSO),
2. a way for a sibling SPA to obtain a raw JWT (for WebSocket `?token=` calls),
3. CORS and CSP headroom so the browser does not silently block cross-app requests.

Neither GAM nor GVC will have its own registration, password reset, or 2FA. They verify
Gecko Notes' JWT with the shared `JWT_SECRET_KEY` and keep a shadow user row keyed by
the token's `sub`.

## Starting state (verified, not assumed)

- Auth is an **HS256 JWT** created in `backend/app/auth.py`
  (`ALGORITHM = "HS256"`, `ACCESS_TOKEN_EXPIRE_DAYS = 30`), claims `{sub, username, exp}`.
  There are **no refresh tokens** and no revocation list.
- The token is returned in the JSON body by `_finalize_login()` at
  `backend/app/routers/auth.py:63`, used by both `POST /login` (line 219) and
  `POST /login/2fa` (line 244).
- The browser stores it in `localStorage` (`auth_token`, `auth_user`) — see
  `frontend/src/stores/auth.ts:136`.
- It is attached as `Authorization: Bearer …` by an axios request interceptor at
  `frontend/src/api/client.ts:13`, and **duplicated in three more places** that bypass
  axios: `frontend/src/api/stream.ts:30`, `frontend/src/api/deepgramStream.ts:40`,
  `frontend/src/api/fluxStream.ts:45`.
- It is verified by a **global HTTP middleware**, `jwt_auth_middleware` at
  `backend/app/main.py:120`, which rejects anything without a Bearer header. There is
  **no** `Depends(get_current_user)`; routers read `request.state.user_id`.
- **There is no `set_cookie` call anywhere in the backend** and **no `/logout`
  endpoint** — logout is purely `localStorage.removeItem`.
- `JWT_SECRET_KEY` is doubly load-bearing: it also derives the Fernet key that encrypts
  stored provider API keys (`backend/app/auth.py:_fernet`). **It cannot be rotated
  independently of those keys.**

`localStorage` is origin-scoped, so `notes.geckopico.com` and `gam.geckopico.com`
cannot read each other's. A cookie scoped to the parent domain is what crosses that
boundary — hence GN-1.

---

## GN-1 — Parent-domain session cookie  *(required)*

Issue an HttpOnly cookie on the parent domain **in addition to** the existing JSON
token, and teach the middleware to accept it as a fallback. Header keeps precedence, so
Gecko Notes' own frontend is unaffected.

### 1a. Config

`backend/app/routers/auth.py` (near the other module constants):

```python
AUTH_COOKIE_NAME = "gecko_session"
# "" (dev) -> a host-only cookie. ".geckopico.com" -> sent to every subdomain.
AUTH_COOKIE_DOMAIN = os.getenv("AUTH_COOKIE_DOMAIN", "").strip() or None
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "true").lower() != "false"
```

`AUTH_COOKIE_SECURE` exists so local `http://localhost:5173` development still works;
it must stay `true` in production.

### 1b. Set the cookie on login

`_finalize_login()` currently returns a `Token` model, so it cannot set a header. Give
it the `Response` FastAPI already injects.

```python
def _finalize_login(session: Session, user: User, response: Response) -> Token:
    ...
    token = create_access_token({"sub": user.id, "username": user.username})
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=ACCESS_TOKEN_EXPIRE_DAYS * 24 * 3600,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite="lax",
        domain=AUTH_COOKIE_DOMAIN,
        path="/",
    )
    return Token(access_token=token, token_type="bearer", user=UserRead.model_validate(user))
```

Add `response: Response` to the signatures of `login` (line 219) and
`login_two_factor` (line 244) and pass it through. Both call sites are already listed
above; there are only two.

`samesite="lax"` is deliberate: `strict` would drop the cookie on the top-level
navigation from GAM to Notes and back, which is exactly the flow being built.

### 1c. Add a logout endpoint

There is none today, and a JS-invisible cookie cannot be cleared from the client.

```python
@router.post("/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie(
        AUTH_COOKIE_NAME, domain=AUTH_COOKIE_DOMAIN, path="/", samesite="lax"
    )
```

`delete_cookie` must be given the **same** `domain` and `path` used to set it, or the
browser keeps the original. Add `/api/auth/logout` to `PUBLIC_PATHS` in
`backend/app/main.py:44` — logging out must work even with an expired token.

Then call it from `frontend/src/stores/auth.ts:110` (`logout()`), before the
`localStorage.removeItem` calls, ignoring any failure so logout never gets stuck.

### 1d. Accept the cookie in the middleware

`backend/app/main.py`, inside `jwt_auth_middleware` (line 120). Replace the
header-only extraction:

```python
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = request.cookies.get(AUTH_COOKIE_NAME)
        if token and not _origin_allowed_for_cookie(request):
            return JSONResponse(status_code=403, content={"error": {
                "code": "forbidden_origin",
                "message": "Cookie authentication requires an allowed Origin",
            }})

    if not token:
        return JSONResponse(status_code=401, content={"error": {
            "code": "unauthorized",
            "message": "Missing or invalid Authorization header",
        }})
```

The rest of the function (the `decode_token` try/except setting
`request.state.user_id` / `request.state.username`) is unchanged.

### 1e. CSRF guard — the one real risk this change introduces

**Today Gecko Notes needs no CSRF protection precisely because auth is a header.** A
cookie is an ambient credential the browser attaches to cross-site requests on its own,
so adding one without a guard would make every state-changing endpoint CSRF-able.

```python
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

def _origin_allowed_for_cookie(request: Request) -> bool:
    """Cookie-authenticated writes must come from a known origin.

    Only applies to the cookie path: a Bearer header cannot be attached by a
    cross-site form or image, so header-authenticated requests are exempt. Safe
    methods are exempt too — they change nothing.
    """
    if request.method in _SAFE_METHODS:
        return True
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if not origin:
        return False          # fail closed: a same-origin browser write always sends one
    return any(origin.startswith(o) for o in _cors_origins)
```

`_cors_origins` is already computed at `backend/app/main.py:98`. Note this reuses the
`CORS_ORIGIN` allowlist, so GN-3 must be applied for a sibling app's writes to pass.

### 1f. Env plumbing

`.env.example` — new section:

```env
# Suite SSO. Set to the parent domain so one login covers every subdomain.
# Leave blank for local development (yields a host-only cookie).
AUTH_COOKIE_DOMAIN=.geckopico.com
# Only set false for local http development.
AUTH_COOKIE_SECURE=true
```

`docker-compose.yml`, `backend.environment` — following the existing explicit style:

```yaml
      - AUTH_COOKIE_DOMAIN=${AUTH_COOKIE_DOMAIN:-}
      - AUTH_COOKIE_SECURE=${AUTH_COOKIE_SECURE:-true}
```

### 1g. Tests

New `backend/tests/test_auth_cookie.py`. Note this repo has only one existing
`TestClient` test (`tests/test_media_range.py`) — follow its setup.

1. `POST /api/auth/login` sets `gecko_session`; it is HttpOnly and its `Max-Age` matches 30 days.
2. A request with only the cookie authenticates.
3. A request with **both** a valid cookie and a valid header uses the header (assert the
   header's `sub` wins when the two differ).
4. `POST /api/auth/logout` clears it; a subsequent cookie-only request 401s.
5. `POST` authenticated by cookie with `Origin: https://evil.example` → **403**.
6. `POST` authenticated by cookie with **no** `Origin` → **403** (fail closed).
7. `GET` authenticated by cookie with a foreign `Origin` → **200** (safe method).
8. `POST` authenticated by **header** with a foreign `Origin` → **200** (header is exempt).
9. `AUTH_COOKIE_DOMAIN` unset → the `Set-Cookie` carries no `Domain` attribute.

---

## GN-2 — `GET /api/auth/session`  *(required)*

The cookie authenticates HTTP calls, but a sibling SPA still needs the raw token for the
WebSocket `?token=` pattern this repo uses (`stt_stream.py:41`, `flux_stream.py`), and
to bootstrap without shipping a login form.

`backend/app/routers/auth.py`:

```python
@router.get("/session", response_model=Token)
def current_session(request: Request, session: Session = Depends(get_session)):
    """Exchange a valid session (cookie or header) for a token plus the user.

    This is what lets a sibling app in the suite start up signed in. It mints a
    fresh token rather than echoing the presented one, so the sibling's copy has
    its own full lifetime.
    """
    user = _require_auth(request, session)
    token = create_access_token({"sub": user.id, "username": user.username})
    return Token(access_token=token, token_type="bearer",
                 user=UserRead.model_validate(user))
```

Leave it **out** of `PUBLIC_PATHS` — the middleware must authenticate it. It is a `GET`,
so the CSRF guard does not apply.

GAM's flow: `GET https://notes.geckopico.com/api/auth/session` with
`credentials: "include"` → 200 means signed in; 401 means redirect to
`https://notes.geckopico.com/login?redirect=<gam url>`.

**Prerequisite:** `LoginView` must honour a `redirect` query param and send the browser
back there after login. Check `frontend/src/views/LoginView.tsx` — it currently uses
`location.state.from` (set by `ProtectedRoute` in `App.tsx`), which only covers
in-app navigation. Add a `?redirect=` fallback, validated against an allowlist of
`*.geckopico.com` origins so it cannot be turned into an open redirect.

---

## GN-3 — CORS and CSP headroom  *(required)*

Two one-line changes; without them the browser blocks the calls silently.

**CORS** — config only. `CORS_ORIGIN` is already a comma-separated exact-origin list
(`backend/app/main.py:95`) and `allow_credentials=True` is already set (line 103). In
production `.env`:

```env
CORS_ORIGIN=https://notes.geckopico.com,https://gam.geckopico.com
```

Note the current value may be a single origin or empty; **append, do not replace**. This
list is also what GN-1e's CSRF guard checks against.

**CSP** — `frontend/nginx.conf`. The policy today contains `connect-src 'self'`, which
blocks any call from the Notes SPA to GAM's API. Widen it, and `img-src`/`media-src`
too if Notes is ever to render a GAM thumbnail inline:

```
connect-src 'self' https://gam.geckopico.com;
img-src 'self' data: blob: https://gam.geckopico.com;
media-src 'self' blob: https://gam.geckopico.com;
```

Leave `script-src` alone — `'unsafe-inline' 'unsafe-eval'` is there because the editor
requires it, and nothing here changes that.

---

## GN-4 — Asset-reference convention  *(decision, minimal code)*

The strategy doc left this open: `[asset:123]` shortcode vs a link to
`gam.geckopico.com/a/123`.

**Use a plain link.** BlockNote renders links already, whereas a shortcode needs a
custom block spec, a parser, *and* handling in all six export paths this repo supports
(PDF, DOCX, Markdown, HTML, MP3, MP4 — `frontend/src/utils/`). That is a lot of surface
for a syntax whose only advantage is brevity.

Phase 1 therefore needs **no code change in this repo** — a GAM asset is referenced by
pasting its URL. A custom `geckoAsset` block (which would live in `frontend/src/blocks/`
alongside the existing `videoFile` / `noteReference` specs) is worth building only when
inline previews are actually wanted, and should be scoped separately.

---

## GN-5 — Explicitly NOT in this phase

The strategy doc's Phase 2 proposes pointing Gecko Notes' media store at GAM. **Do not
do this yet.** Notes keeps `./data/media/` and its `NoteAsset` bookkeeping; GAM is
purely additive until it has proven itself in use.

Migrating a live note corpus's media means rewriting every `/media/...` URL inside every
`Note.content` **and** every `NoteVersion.content` snapshot, with a rollback story for a
half-finished run. That is its own project, and doing it early risks the app that
currently works.

---

## GN-6 — Unrelated findings  *(offered, not required)*

Found while reading; each is independent of the above.

1. **Video renders silently use the wrong font.** `backend/app/video/compose.py:22`
   points `FONT_DIR` at `backend/assets/fonts/`, which does not exist in the repo — the
   Inter TTFs are at `backend/app/assets/fonts/`, and the Dockerfile only does
   `COPY app/ ./app/`. Every render today falls through `_FALLBACKS` to DejaVuSans.
   One-line path fix; worth a test asserting the resolved font file exists.

2. **`/media/*` is completely unauthenticated.** It is on the public-path allowlist
   (`backend/app/main.py:_is_public`), so every upload in every private note is readable
   by anyone holding the UUID URL. This is presumably what makes shared notes work, but
   it is worth an explicit decision rather than an inherited one. GAM deliberately does
   not copy it (it uses expiring signed URLs instead), so there is a working pattern to
   backport if you want one.

3. **`AppConfig` / `configApi` is declared twice** — `frontend/src/api/notes.ts`
   (2 fields) and `frontend/src/api/config.ts` (6 fields). Whichever import a component
   picks changes what it sees.

---

## GN-7 — python-jose is on five CVEs  *(security, independent of GAM)*

`backend/requirements.txt:10` pins `python-jose[cryptography]==3.3.0`, released in 2021.
PyPI reports five advisories against it, all fixed in 3.4.0:

| Advisory | What it is |
|---|---|
| CVE-2024-33663 | Algorithm confusion with OpenSSH ECDSA and other key formats — the class that ends in forged tokens |
| CVE-2024-33664 | DoS via a crafted JWE during decode |
| CVE-2024-29370 | DoS in `jwe.decrypt` (listed with no fixed version) |

This is the library Notes uses to sign and verify every session token, so it is worth
reading carefully rather than filing as a routine bump.

**How exposed Notes actually is: less than that table suggests.** `backend/app/auth.py:47`
decodes as `jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])` — an explicit
single-algorithm allowlist, which is the documented mitigation for CVE-2024-33663. The two
DoS advisories are in the JWE paths, and Notes issues JWS. So this closes a gap and
removes a dated dependency; it is not a live hole, and it does not warrant an emergency
deploy.

**The change:** `python-jose[cryptography]==3.3.0` → `==3.5.0`. No API change — GAM made
exactly this bump and its 352 tests passed untouched, auth tests included. Keep the
explicit `algorithms=` allowlist regardless of version; it is good practice independent
of any CVE.

There is a second reason beyond the advisories. 3.3.0's `jose/jwt.py` calls
`datetime.utcnow()`, which is deprecated from Python 3.12 and *scheduled for removal*.
When it goes, JWT verification raises on import and every login in Notes fails. 3.5.0
uses `datetime.now(UTC)` instead. That failure would arrive on a routine Python upgrade,
with no code change in Notes to point at.

---

## GN-8 — Python version  *(housekeeping, do it with GN-7)*

Notes runs Python 3.11 (`backend/Dockerfile:1`, `.github/workflows/ci.yml:44`). GAM has
moved to 3.13 and the suite should stay aligned, since the two repos share vendored
modules and backport fixes between each other.

It costs nothing in dependencies. Checked against Notes' own `requirements.txt`: Pillow
10.4.0 publishes cp313 wheels, bcrypt 4.0.1 ships `abi3`, and everything else pinned there
is `py3-none`. So it is two lines — `python:3.11-slim` → `python:3.13-slim`, and
`python-version: '3.11'` → `'3.13'` — plus a `.python-version` file holding `3.13`, which
is what makes a contributor's system interpreter stop mattering (`uv venv` reads it and
downloads the right one).

Do **not** go to 3.14 without budgeting for it: Pillow 10.4.0 has no 3.14 wheel, so it
would force Pillow to 12.x, and in Notes that lands in `video/compose.py` — the renderer,
which has close to no test coverage. That is what made GAM choose 3.13. The symptom if
someone tries it anyway is `Failed building wheel for Pillow`, which names neither Python
nor the version, and which is how this was found.

---

## Order of application

GN-3 (config) → GN-1 (cookie + CSRF + tests) → GN-2 (session endpoint + redirect
handling). GN-1 without GN-3 will fail the CSRF guard for sibling-app writes.

Ship GN-1 and verify Gecko Notes itself still works **before** GAM depends on it: the
existing frontend uses the header path throughout, so a correct implementation is
invisible to it. If anything in Notes breaks, the cookie is the cause.
