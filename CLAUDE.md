# gam — Claude Instructions

Gecko Asset Manager. Part of the Gecko Suite alongside
[gecko-notes](https://github.com/davior/gecko-notes) (live, the identity provider) and
Gecko Video Creator (not yet started).

Read [`docs/plan-of-attack.md`](docs/plan-of-attack.md) before starting a milestone — it
carries the architecture, the milestone order, and the reasoning behind decisions that
would otherwise look arbitrary.

## Working with gecko-notes

GAM deliberately mirrors gecko-notes' stack and conventions, and vendors several of its
modules. When you need to know how that repository does something, **read its source,
not its README**. The README overstates what exists in three places that already cost
one round of wrong assumptions:

- there is no auth cookie (auth is a Bearer JWT from `localStorage`),
- its `NoteAsset` table is per-note bookkeeping, not an asset library,
- there is no search of any kind — no FTS5, no embeddings, just `ILIKE '%term%'`.

Changes GAM needs *from* gecko-notes are specified in
[`docs/gecko-notes-integration.md`](docs/gecko-notes-integration.md). Do not apply them
from here; that is a separate repository.

## Code conventions

These are followed in gecko-notes but written down nowhere, so they are written down
here.

**API contract**
- Single resources: `{"data": T}`. Lists: `{"data": [T], "total", "limit", "offset"}`.
- Errors: `{"detail": {"code": "snake_case_code", "message": "human sentence"}}`.

**Backend**
- All environment configuration goes through `app/config.py`. Never `os.getenv` at
  module scope — that is what makes gecko-notes' tests monkeypatch module globals.
- Schema changes are Alembic migrations. Never a `try: ALTER TABLE …; except: pass`.
- Get the caller with the `CurrentUser` dependency. Do not hand-roll a `_get_user_id`
  helper per router.
- Structured data is JSON-as-TEXT in a `str` column, decoded on read.
- Primary keys are `str(uuid.uuid4())` generated at the call site.
- Timestamps are naive UTC, named `created_at` / `updated_at` consistently, and come
  from `app.clock.utcnow` — never `datetime.utcnow()` (deprecated, scheduled for
  removal) and never `datetime.now(UTC)` (aware, so it raises TypeError the moment
  anything subtracts it from a value read back out of the database).
- Tests go through `TestClient` against real routes, not by calling router functions
  directly.

**Frontend**
- Types live beside the api module that owns them. There is no `types/` directory.
- One `xApi = { … }` object per backend router, in `src/api/`, over the shared axios
  client.
- Zustand stores without middleware; guard every await with a staleness check before
  `set(...)`; expose `reset()` and fan it out on logout.
- Imports use the `@/` alias. No barrel `index.ts` files.
- 2-space indent, single quotes, no semicolons — enforced by Prettier.

**Comments** explain *why*, not *what*. A comment restating the code is noise; a comment
recording a constraint, a rejected alternative or a load-bearing subtlety is the most
valuable thing in the file. This is gecko-notes' house style and it is worth keeping.

## After every change (dev environment)

Dev ports are 8001 and 5174, not 8000 and 5173 — gecko-notes uses those and both
apps run at once. `vite.config.ts`'s proxy target has to match the port uvicorn is
started on, or the frontend reaches gecko-notes' backend, which shares
`JWT_SECRET_KEY` and answers instead of erroring.

The backend runs on **Python 3.13**, pinned by `.python-version` and matched by
`backend/Dockerfile` and CI. Not "3.13 or newer" — the pinned Pillow and numpy ship
wheels no further than 3.13, and on a newer interpreter pip silently compiles them from
source and fails there. Build the venv with `uv venv` (which reads `.python-version`)
or `python3.13 -m venv`, never a bare `python3`.

Migrations do not run here. `alembic upgrade head` lives in `backend/entrypoint.sh`,
the Docker entrypoint; the app deliberately never migrates on startup, so a failed
migration stops the container rather than leaving it serving a schema it does not match.
Nothing runs it for a bare `uvicorn`, so a pull that adds a table turns every request
touching it into `no such table: …`. Run it after any pull that adds a migration.

```bash
# Backend — http://localhost:8001 (auto-reloads on save)
cd backend
alembic upgrade head                      # not automatic outside Docker
uvicorn app.main:app --reload --port 8001

# Frontend — http://localhost:5174 (Vite HMR; proxies /api and /media to :8001)
cd frontend && npm run dev
```

Under Docker the frontend is a compiled bundle served by Nginx, so source edits need a
rebuild — a plain `docker compose up -d` keeps serving the previous one:

```bash
docker compose up --build -d
```

Then hard-refresh (Ctrl/Cmd+Shift+R) to bypass cached assets.

## Before pushing

```bash
cd backend && pytest -q
cd frontend && npm test && npm run build    # build also typechecks
```

A push that turns CI red costs a cycle. Run both suites first.

## After committing to a non-main branch

Report the pull request for that branch — number, URL, title, draft and CI status.
If none exists, open a **draft** PR and report it.

## After every commit to main (production deployment)

```bash
git pull origin main
docker compose up --build -d
docker compose ps
curl -f http://localhost:${APP_PORT}/api/health
```

Notes:
- `data/db/` and `data/media/` are bind-mounted, so they survive rebuilds.
- Migrations run on backend startup.
- `JWT_SECRET_KEY` must be set, and must **match gecko-notes'** — it is what lets GAM
  verify a session Notes issued. The app refuses to start without it.

### Behind a reverse proxy

Set `COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml` in `.env` so the prod
overlay is always included. It joins the frontend to the external `web` network the
reverse proxy uses; without it the container is isolated and the proxy returns 502.
