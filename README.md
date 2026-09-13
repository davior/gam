# Gecko Asset Manager (GAM)

The media library for the [Gecko Suite](#the-gecko-suite) — a searchable, persistent
home for every image, video, audio file, document and AI generation you accumulate, so
that a clip you saw once is findable a year later.

The problem GAM solves is **recall**. Media piles up; what gets lost is the *moment* —
the ten seconds inside a ninety-minute interview where someone said the thing you now
want to quote. GAM transcribes, indexes and searches that material so a plain-English
question returns the asset *and the timestamp*.

> **Status: early development.** The scaffold is being built milestone by milestone.
> See [`docs/plan-of-attack.md`](docs/plan-of-attack.md) for the roadmap and the
> reasoning behind the architecture.

## The Gecko Suite

Three loosely-coupled apps under one domain and one login:

| App | Role | Status |
|---|---|---|
| [Gecko Notes](https://github.com/davior/gecko-notes) | The writing hub — scripts, narration, drafts. Source of truth for narrative. | Live at `notes.geckopico.com` |
| **Gecko Asset Manager (GAM)** | The media library. Source of truth for assets. | This repository |
| Gecko Video Creator (GVC) | The output engine — combines notes and assets into finished video. | Not yet started |

Assets flow GAM → GVC; narrative flows Notes → GVC. Each app is independently
deployable and owns its own data; they share identity and talk over APIs rather than
reaching into each other's databases.

Gecko Notes is the **identity provider** for the suite. GAM has no registration,
password reset or 2FA of its own — it verifies the JWT Notes issues and keeps a shadow
user row. The changes that makes possible are specified in
[`docs/gecko-notes-integration.md`](docs/gecko-notes-integration.md).

## Planned capabilities

- **Ingest with no friction** — drop files in with a name and nothing else. Everything
  beyond that is optional and can be filled in later, by hand or by AI.
- **Find the moment** — full-text search over names, descriptions, summaries, tags and
  transcripts, fused with semantic search so half-remembered wording still lands. Hits
  in audio and video come back with a timestamp you can jump straight to.
- **Clips and sub-videos** — mark a range non-destructively (no new file, always in step
  with its parent), or physically extract it as a standalone asset.
- **Opt-in AI enrichment** — transcription, image description, summaries and tag
  suggestions. Suggestions are reviewed, never applied silently, and a field you edited
  by hand is not overwritten by a later AI run.
- **AI asset creation** — generate images from images, and video from one or more
  images, with the prompt and base assets recorded so a result stays reproducible.
- **Cost visibility** — an estimate before anything paid runs, and the real cost tracked
  per asset and per library afterwards.

## Tech stack

Matches Gecko Notes, so the two stay maintainable together:

| Layer | Technology |
|---|---|
| Frontend | React 18 + Vite + TypeScript + Zustand + Tailwind CSS v3 |
| Backend | FastAPI + SQLModel (SQLite), Alembic migrations |
| Media | FFmpeg (probing, thumbnails, extraction) |
| Search | SQLite FTS5 + vector embeddings, fused |
| Transcription | Deepgram |
| Generation | fal.ai |
| Container | Docker Compose + Nginx |

## Development

Requires Python 3.11+, Node 20+, and `ffmpeg`/`ffprobe` on `PATH`.

```bash
# Backend — http://localhost:8000
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000

# Frontend — http://localhost:5173 (proxies /api and /media to :8000)
cd frontend
npm install
npm run dev
```

Or run the whole stack:

```bash
cp .env.example .env     # JWT_SECRET_KEY is required
docker compose up --build -d
```

For a real deployment on `gam.geckopico.com`, see
[`docs/deployment.md`](docs/deployment.md).

`JWT_SECRET_KEY` **must match the value Gecko Notes uses** — that shared secret is what
lets GAM verify a session Notes issued. Generate one with `openssl rand -hex 32` if you
are running GAM standalone.

## Tests

```bash
cd backend && pytest -q
cd frontend && npm test && npm run build
```

## Backup

Two things to back up: the SQLite database at `./data/db/` and the media tree at
`./data/media/`. Media filenames are write-once UUIDs, which is what lets an incremental
backup send only what is new.

## License

See [LICENSE](LICENSE).
