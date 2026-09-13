# Deploying Gecko Asset Manager

GAM runs as a Docker Compose stack behind the same reverse proxy as Gecko Notes, on
`gam.geckopico.com`. It shares an identity with Notes rather than having its own.

## What has to be true first

**Gecko Notes must be running the suite SSO changes.** They are on `main` as of
[`95ed2ca`](https://github.com/davior/gecko-notes/commit/95ed2ca) — the session cookie,
`GET /api/auth/session`, and the CORS/CSP headroom. GAM verifies the cookie Notes sets;
without those changes there is no cookie to verify and nobody can sign in.

The full specification, including the reasoning, is in
[`gecko-notes-integration.md`](gecko-notes-integration.md).

## The one setting that must match

```env
JWT_SECRET_KEY=<the same value gecko-notes uses>
```

This is not a recommendation. GAM issues no tokens — it verifies the ones Notes signs,
and a shared HS256 secret is the whole mechanism. A different value rejects every
session, and the symptom is an unhelpful "sign in" loop rather than a clear error.

The same secret also derives the key encrypting stored provider credentials, so
changing it invalidates every session **and** every saved API key.

## Gecko Notes side

In Notes' `.env`:

```env
# Sends the session cookie to every subdomain rather than only notes.geckopico.com.
AUTH_COOKIE_DOMAIN=.geckopico.com
```

Restart Notes. Its own frontend is unaffected — it authenticates by header, and the
cookie is additional.

`CORS_ORIGIN` on Notes only needs `https://gam.geckopico.com` if GAM's browser code
calls Notes' API directly. It does not today: GAM reads the shared cookie on its own
origin. Add it if that changes.

## GAM side

```bash
git clone https://github.com/davior/gam.git
cd gam
cp .env.example .env
```

Edit `.env`:

```env
APP_PORT=18082                      # 18081 is gecko-notes
JWT_SECRET_KEY=<same as Notes>
NOTES_BASE_URL=https://notes.geckopico.com
COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml
```

`COMPOSE_FILE` is what joins the containers to the reverse proxy's external `web`
network. Without it they sit on `gam_default`, unreachable from the proxy, and every
request is a 502. Setting it in `.env` means a plain `docker compose up -d` cannot
forget.

```bash
docker compose up --build -d
docker compose ps
curl -f http://localhost:18082/api/health
```

`{"status":"ok","version":"…","ffmpeg":true}` is the expected reply. **`ffmpeg: false`
means the image is wrong** — probing, thumbnails and transcription all shell out to it.

## Reverse proxy

Point `gam.geckopico.com` at the `frontend` container on port 80, the same way
`notes.geckopico.com` is routed. Nothing in this repository needs to change; the
container listens on plain HTTP and the proxy terminates TLS.

The backend is `expose`d, never `ports:`-published. It trusts `X-Forwarded-*` from any
peer, which is correct behind a controlled proxy and a spoofing vector if the port is
reachable directly.

## Verifying the sign-in flow

1. Sign in at `notes.geckopico.com`.
2. Open `gam.geckopico.com` in the same browser. It should load the library with no
   second login.
3. Sign out of Notes, reload GAM: it should offer "Sign in with Gecko Notes".
4. Click it, sign in, and land back on GAM — the `?redirect=` parameter carries the
   return address, validated by Notes against `*.geckopico.com`.

If step 2 shows a sign-in prompt while Notes is signed in, the cookie is not reaching
GAM. Check that `AUTH_COOKIE_DOMAIN` is `.geckopico.com` (with the leading dot) and that
the browser shows a `gecko_session` cookie scoped to the parent domain.

## Backups

Inert until configured. Fill in the `RESTIC_*` and `BACKUP_SFTP_*` settings in `.env`
and follow [`ops/backup/README.md`](../ops/backup/README.md), which documents the two
Synology DSM gotchas that cost an afternoon the first time.

Two things are backed up: `./data/db/gam.db` and `./data/media/`. The database is
snapshotted with `sqlite3 .backup` so a WAL-mode file is captured consistently; media
filenames are write-once UUIDs, which is what lets restic send only what is new.

GAM and Notes can share one Synology and one restic repository — the distinct
`--host` tag and `BACKUP_SFTP_REMOTE_PATH` keep their snapshots apart, so
`restic forget` prunes each app's history separately.

## Transcription

Nothing to configure at deploy time. Each user adds their own Deepgram key at
**Settings → Speech to text**; it is encrypted with a key derived from
`JWT_SECRET_KEY` before it is stored, and is never returned to the browser.

## Updating

```bash
git pull origin main
docker compose up --build -d
```

`data/db/` and `data/media/` are bind mounts, so they survive rebuilds. Migrations run
in the backend's entrypoint before uvicorn binds a port — a failed migration stops the
container rather than leaving a running app serving a schema it does not match.
