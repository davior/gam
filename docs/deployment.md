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

Optionally the same run also keeps a **plain-file mirror** on the NAS (`BACKUP_MIRROR_PATH`,
step 13 of the backup README): the same `db/` + `media/` layout as `data/`, as ordinary
files. A read-only copy of the app can run from it in Synology's Container Manager, with
no dependency on this host or on Notes — see [`ops/archive/README.md`](../ops/archive/README.md).
That copy sets `READ_ONLY=true` and `READ_ONLY_USER`; the app refuses to start with the
second and not the first, so neither can turn sign-in off here by accident.

## Disk space

**Settings → Storage** shows what the library takes up and what the disk has left. Every
user sees their own library, broken down by type, with their largest files as links. An
[administrator](#administrators) also sees the server:

- **One bar per disk**, split into GAM's media, GAM's database, everything else on that
  disk, and free space. These are the host's numbers: `data/` is a bind mount, so the
  container sees the real disk. "Everything else" is whatever else lives there, such as
  the OS, Docker's images and Gecko Notes. GAM cannot break that part down from inside its
  container; `docker system df` on the host shows Docker's own share.
- **Reserved by the system**, when there is any. ext4 keeps about 5% of a disk back for
  root, so used plus free falls short of the total by that much. Free space is shown as
  what can actually be written, never as total minus used.
- **Working space**, when the container's temp directory is on a different disk.
  Uploads and imports land there before they are stored. It is shown, but it does not
  count toward the warning: nothing persistent lives there, and on a development machine
  it is often a small RAM-backed tmpfs.
- **Stray files**: files under `data/media` that no asset refers to, left by a delete
  whose unlink failed or by an upload or job that died between writing the file and
  saving the row. Only files more than a day old count, because every write puts the
  file on disk before it saves the row. **Remove them** deletes them. The hourly backup
  still has them if that turns out to be a mistake.

Two thresholds, in GB of free space:

```env
DISK_WARN_FREE_GB=20    # below this, a drive icon appears in the header
DISK_RESERVE_GB=5       # below this, new writes are refused
```

Below the reserve, uploads, URL imports, AI generations and clip extractions are refused
with "the server's disk is nearly full". They are checked when queued and again when the
job starts. A generation is checked before it is sent to fal, so nothing is billed for an
output that could not be saved; one already submitted still saves. Clips, tags,
transcripts and previews are not refused, since they write next to nothing. The
thresholds are fixed sizes rather than percentages because what fills the disk is single
large files: a 90-minute import is 1–2 GB whatever size the disk is.

## Transcription

Nothing to configure at deploy time. Each user adds their own Deepgram key at
**Settings → Speech to text**; it is encrypted with a key derived from
`JWT_SECRET_KEY` before it is stored, and is never returned to the browser.

## Semantic search

Also nothing at deploy time, and also per user: **Settings → Semantic search**. Two
providers, and the choice is a privacy decision as much as a cost one.

- **OpenAI** needs an API key, stored the same encrypted way as the Deepgram one. Text
  — transcripts included — is sent to OpenAI to be embedded.
- **Ollama** needs no key, only an address (default `http://localhost:11434`) and the
  model pulled on that host. Nothing leaves the machine.

Until a provider is set, search still works but matches words only, and says so. Newly
transcribed assets are embedded automatically; anything already in the library needs
`POST /api/assets/{id}/embed`, since embedding a whole library at once is bulk
enrichment and belongs with the rest of it in M6.

Changing the model later does not invalidate what is stored — vectors record the model
that produced them and only matching ones are searched — so a switch quietly shrinks
the searchable set until the affected assets are embedded again.

## Administrators

Gecko Notes' token says who someone is, not whether they are an admin, so GAM is told
in `.env`:

```env
ADMIN_USERS=1f0c…            # Notes user ids (or usernames), comma-separated
```

Both match exactly, case included. Prefer the user id — the `sub` in a Notes token, and
the `id` `GET /api/me` returns. Notes keeps usernames unique only case-sensitively and
lets anyone rename themselves, so a name is only as safe as nobody else ever holding it,
and an admin decides which fal endpoint every user's prompts and base images go to.

An admin is the only one who can edit
the AI generation model catalogue (Settings → Generation); everyone else uses it as it
stands. Left empty, nobody can edit it and the catalogue the migration seeded is what
everyone gets — which works, until fal retires one of those endpoints.

An admin also sees the server's disk under **Settings → Storage**, and can clear stray
files there. Everyone else sees only their own library's share.

## AI generation

Per user, like transcription: each person adds their own fal.ai key at **Settings →
Generation**, and fal bills that account. Nothing to configure at deploy time beyond
`ADMIN_USERS` above. The `GENERATION_*` settings in `.env.example` tune concurrency,
the timeout and the size limits; the defaults suit one host.

## Importing from YouTube

Nothing to configure unless YouTube refuses the server. Many VPS and datacenter IP ranges
get *"Sign in to confirm you're not a bot"*, and age-restricted videos always need a
signed-in session. For either, export a cookies file from a Firefox profile made for the
purpose, put it at `secrets/youtube-cookies.txt`, and add `docker-compose.cookies.yml` to
`COMPOSE_FILE`:

```env
COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml:docker-compose.cookies.yml
```

The full steps — the Firefox export, keeping only YouTube's cookies, and why a spare
account — are in [`url-import.md` → YouTube cookies](url-import.md#youtube-cookies).
Not `docker-compose.override.yml`: with `COMPOSE_FILE` set, Compose never reads it.

When imports start failing across the board, YouTube has changed under yt-dlp: bump its
pin in `backend/requirements.txt` and rebuild.

## Updating

```bash
git pull origin main
docker compose up --build -d
```

`data/db/` and `data/media/` are bind mounts, so they survive rebuilds. Migrations run
in the backend's entrypoint before uvicorn binds a port — a failed migration stops the
container rather than leaving a running app serving a schema it does not match.
