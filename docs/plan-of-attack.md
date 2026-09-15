# Gecko Asset Manager — Plan of Attack

## Context

`davior/gam` is an empty repository — no commits, not even a `main` branch. This plan
covers the genesis commit through a deployable Phase 1 GAM, plus a self-contained
change spec for `davior/gecko-notes`.

GAM is the middle layer of the Gecko Suite: Gecko Notes (narrative, live at
notes.geckopico.com) → GAM (media) → GVC (output, not yet started). The problem GAM
solves is recall: a creator accumulates hundreds of hours of video and cannot find
*the moment* again. "The video where Giordano talks about deploying nano weapons" is
the acceptance test for the whole product.

### What reading gecko-notes changed about the strategy doc

The strategy doc was written against the README. Reading the code contradicts it in
three places that matter, and the plan below reflects the corrections:

1. **There are no auth cookies.** Not one `set_cookie` call exists in the backend.
   Auth is an HS256 JWT (30-day, no refresh, claims `{sub, username, exp}`) in
   `localStorage`, sent as `Authorization: Bearer`, verified by a global HTTP
   middleware at `backend/app/main.py:120`. "Widen the cookie to `.geckopico.com`"
   is not a config tweak — the cookie is new work, specified as GN-1 below.

2. **Gecko Notes does not already contain "most of GAM".** `NoteAsset`
   (`backend/app/models.py:114`) is *per-note bookkeeping* over a flat media tree, and
   `/api/assets` is only an orphan-file reclaim scan. There is no catalogue, no
   library-wide listing, no tags, no clips, and **no search of any kind** — note search
   is `ILIKE '%term%'` over raw BlockNote JSON (`routers/notes.py:376`); there is no
   FTS5 and no embeddings anywhere in the repo. GAM's core is greenfield.

3. **What *is* reusable is better than advertised** — and it is infrastructure, not
   asset management: the background-job system (`app/jobs/`, ~560 lines,
   kind-agnostic, with heartbeats, stale sweeping, restart recovery and
   progress-as-cancellation-checkpoint), `media_files.py` (HTTP 206 Range support),
   `thumbnails.py`, `routers/media.py`'s streaming `save_upload` + extension taxonomy,
   `settings.py::_post_upstream` (retry/backoff/timeout translation),
   `_require_safe_external_url` (SSRF guard), Fernet `encrypt_api_key`, and the
   `UsageEvent` + `pricing.py` cost model — which already resolves fal.ai costs
   *exactly* from `x-fal-billable-units` response headers.

### Decisions taken

| Decision | Choice |
|---|---|
| First milestone | GAM vertical slice, not auth extraction |
| SSO | Parent-domain HttpOnly cookie added to Gecko Notes; both backends accept header *or* cookie |
| Storage | Local disk behind a `Storage` protocol |
| Semantic search | Real embeddings in Phase 1, hybrid with FTS5 |

---

## Architecture

### Repo strategy — copy now, extract later (deliberately against the strategy doc)

GAM is a standalone repo mirroring gecko-notes' layout. Phase 1 **vendors** the proven
modules into `gam/backend/app/` rather than creating shared `auth`/`ui`/`shared-types`
packages.

Rationale: a shared package designed against one consumer is a guess. GVC has no spec
yet, and the modules worth sharing are exactly the ones gecko-notes has known warts in
(see "Fix on the way in"). Extraction into `gecko-core` becomes Phase 3 work, when GVC
gives us a second real consumer to design the seams against. The one genuine
cross-cutting concern — verifying a Notes-issued JWT — is ~30 lines; a package for it
is pure overhead.

### Fix on the way in (do not copy verbatim)

These are documented problems in gecko-notes. Fix them as GAM is built; each is a
backport candidate later.

- **`_get_user_id` is copy-pasted into 14 routers.** GAM gets one real dependency:
  `CurrentUser = Annotated[UserCtx, Depends(current_user)]`.
- **`MEDIA_DIR` is resolved independently in 4 modules; config is ad-hoc `os.getenv`
  at module scope** (which is why tests must `monkeypatch.setattr(module, "MEDIA_DIR", …)`).
  GAM gets one `app/config.py` `Settings` object (pydantic-settings).
- **Migrations are 650 lines of `try: ALTER TABLE …; except: pass`** with no version
  table and no downgrade. GAM starts on **Alembic**.
- **`load_*_api_key` is duplicated 5×.** One `load_encrypted_setting(session, user_id, key)`.
- **`routers/settings.py` is 3,284 lines** (providers + speech + images + themes +
  usage + cost). GAM splits this into `app/providers/`, `app/enrichment/`, `app/usage/`.
- **Router tests barely exist** — `TestClient` appears in exactly one test file, and
  `init_db()` is never exercised. GAM gets shared `conftest.py` fixtures (app + client +
  seeded user + tmp storage) and HTTP-level tests from M0.
- **No ESLint/Prettier, and Playwright is installed but entirely unused.** GAM adds
  ESLint + Prettier from day one and either wires Playwright or drops it.
- **`/media/*` is completely unauthenticated** in Notes (`main.py` public-path
  allowlist) — any upload is world-readable to anyone holding the UUID URL. GAM must
  not inherit this; see "Serving bytes" below.

### Backend shape

```
backend/app/
  config.py          Settings (pydantic-settings) — the single source of env truth
  database.py        engine + WAL pragmas (copy), get_session
  auth.py            JWT verify (HS256, shared secret), Fernet key helpers, current_user dep
  storage/           Storage protocol + LocalStorage
  models/            asset, tag, transcript, embedding, job, user, usage
  jobs/              runner.py + registry.py (copied, kind-agnostic)
  ingest/            save_upload, probe (ffprobe), thumbnails (image/video/pdf/audio)
  search/            fts.py (SQLite FTS5), vectors.py (numpy cosine), hybrid.py (RRF)
  enrichment/        transcribe.py (Deepgram), describe.py, summarize.py, autotag.py, embed.py
  generate/          fal.py — text/image→image, image→video
  media/             clips.py, extract.py (ffmpeg sub-video), ranged.py (206 responses)
  providers/         llm.py, deepgram.py, fal.py, _upstream.py (retry/backoff, copied)
  routers/           assets, tags, search, enrichment, generate, clips, activity, media, auth, settings
alembic/
```

### Data model (the genuinely new part)

`Asset` is the catalogue row and covers files, clips, sub-videos and AI output in one
table (a clip is an Asset with `storage_key IS NULL` and a `parent_asset_id`):

```python
class Asset(SQLModel, table=True):
    id: str; user_id: str
    name: str; description: str | None; summary: str | None
    asset_type: str        # image|video|audio|document|clip
    source: str            # local_upload|url|ai_generated|gvc_export|clip|sub_video
    storage_key: str | None    # storage-relative key, NEVER a URL
    original_name, mime_type, format, size_bytes, checksum_sha256
    duration_seconds, width, height, codec
    thumb_key, poster_key
    parent_asset_id, in_point, out_point           # clips / sub-videos
    origin_project_id, origin_reference_id         # GVC provenance
    ai_model, ai_prompt, ai_generated_at, ai_generation_type
    ai_parameters: str      # JSON
    ai_source_assets: str   # JSON — base assets, so a generation stays reproducible
    transcript_model, transcript_language, transcript_status
    field_provenance: str   # JSON {"description":"ai"|"human", …}
    upload_date, modified_date, metadata_modified_date
```

Two deliberate deviations from the SRS schema:

- **`field_provenance`** implements FR 8.1.3 ("manual edits are never overwritten by a
  later AI run without confirmation"). Without it that requirement has no mechanism.
- **Transcripts are rows, not a nested JSON blob.** `TranscriptSegment(id, asset_id,
  user_id, idx, text, start_time, end_time, words JSON)`. FR 10.1.4 requires returning
  *a matching snippet with its timestamp*; that means each segment must be individually
  FTS-indexed and individually embedded, which a blob cannot do. The API still
  serialises the SRS's nested shape on read.

Supporting tables: `Tag(id, user_id, name, category_id)` (lowercased, trimmed, unique
per user), `TagCategory(id, user_id, name, parent_category_id)` — nested filtering via
a recursive CTE, `AssetTag(asset_id, tag_id)` composite PK, `Embedding(owner_kind,
owner_id, model, dim, vector BLOB)`, `EnrichmentJob`, `GenerationJob`, plus `User`,
`UserSetting`, `AppSetting`, `UsageEvent` copied from Notes.

**Identity**: GAM has **no login form and no password column of its own**. Gecko Notes
stays the identity provider. GAM verifies the JWT with the shared `JWT_SECRET_KEY` and
upserts a shadow `User` row keyed by the same `sub` on first sight, hydrated from the
token claims (`GET /api/auth/session` on Notes fills in email/admin when needed). An
unauthenticated visitor is redirected to `notes.geckopico.com/login?redirect=…`.

### Search — the flagship

Two indexes over the same corpus, fused:

- **FTS5**, `tokenize='porter unicode61'`: `asset_fts(name, description, summary,
  tags_text)` and `segment_fts(text)`, synced explicitly from `search/fts.py` on every
  write (not triggers — explicit sync is testable and survives Alembic). `bm25()` for
  ranking, `snippet()` for the excerpt.
- **Embeddings**: each transcript segment, description and summary embedded with
  `text-embedding-3-small` at `dimensions=512` (Ollama `nomic-embed-text` as the free
  local alternative, mirroring Notes' provider-pluggable approach). Vectors stored as
  float32 BLOBs, loaded into a per-user numpy matrix cached in-process and invalidated
  on write. 512 dims × float32 = 2 KB/vector; a 50k-segment library is ~100 MB of RAM
  and a sub-10ms brute-force cosine. `sqlite-vec` is the drop-in upgrade past ~100k
  vectors — not needed at Phase 1 scale, and worth not paying for yet.
- **Fusion**: Reciprocal Rank Fusion over the two ranked lists. No weights to tune, and
  exact-phrase queries and half-remembered ones both land.

A result carries `{asset, score, snippet, start_time}`, and the UI deep-links to
`/assets/{id}?t=412.0`.

### Serving bytes — diverging from Notes on purpose

Notes serves `/media/*` with no auth at all. GAM instead issues **short-lived signed
URLs**: `/media/{key}?exp=…&sig=HMAC(key|exp, JWT_SECRET_KEY)`. This keeps `<video>`
and `<img>` working (no auth header needed on a media element) while making a leaked
URL expire. Range/206 support comes from `ranged.py`, lifted from Notes'
`media_files.py` — required for scrubbing a long video and for clip playback.

### Clips vs sub-videos

- **Clip** (non-destructive, FR 11.1.2): an Asset row; playback seeks the parent and
  stops at `out_point`. Zero storage, zero processing, always reflects the parent.
- **Sub-video** (destructive, FR 11.1.1): an ffmpeg job — `-ss/-to -c copy` for speed,
  re-encode fallback when frame accuracy matters (stream-copy cuts land on keyframes).
- **Parent deletion**: the SRS says "invalidates its clips, with user alerting". Better:
  block the delete, list the dependent clips, and offer *"extract them as sub-videos
  first"* — one click converts references into real files, then the parent goes. Losing
  a curated clip to a parent delete is exactly the kind of data loss an asset manager
  exists to prevent.

### Enrichment

Each is a `JobKind` in the copied registry, so `/api/activity` polling, progress,
cancellation, heartbeats and restart recovery come nearly free.

| Job | Implementation |
|---|---|
| `transcribe` | ffmpeg extracts the audio track (smaller upload), then Deepgram **prerecorded** `POST /v1/listen?model=nova-3&smart_format=true&utterances=true&punctuate=true&diarize=true`. Note: Notes integrates Deepgram *streaming* (`stt_stream.py`, `flux_stream.py`) and TTS, but **not** the prerecorded endpoint — that client is new. Word-level timings are what FR 10.1.4 needs. |
| `describe` | Vision LLM (Anthropic/OpenAI via the `AIProvider` pattern). The SRS says fal.ai for descriptions; fal is a generation platform and its captioning models are weaker than a vision LLM at the "what is in this image, in retrieval-useful words" task. Recommend the LLM; fal stays for *creating* media. |
| `summarize` | LLM over transcript / extracted document text |
| `autotag` | LLM proposing tags, stored `status="suggested"` until accepted (FR 9.1.4 — never applied silently) |
| `extract_text` | pypdf / python-docx / plain read |
| `embed` | Runs after any of the above that produce text |

Cost visibility (FR 8.1.4) reuses `UsageEvent` + `pricing.py` + `compute_fal_cost`
verbatim — including fal's exact billed cost from response headers, which is better
than the estimate the SRS implies.

### GVC integration surface (API built in Phase 1, GVC itself is not)

`GET /api/assets` (the search/filter API), `GET /api/assets/{id}`, and signed media
URLs are the whole contract. Plus one thing worth building once: GAM ships its asset
picker as an **embeddable route** (`/picker?embed=1`) that GVC — and Notes — mount in
an iframe, returning `postMessage({assetId, inPoint, outPoint})`. One picker, three
consumers, no duplicated search UI.

---

## Changes required in Gecko Notes

These are additive and independently shippable. Write them up as
`docs/gecko-notes-integration.md` in the GAM repo — a self-contained spec a session on
`davior/gecko-notes` can execute without this context.

**GN-1 — Parent-domain session cookie (required for SSO).**
- `backend/app/routers/auth.py`: in `_finalize_login`, alongside the existing JSON
  `access_token`, also set `gecko_session` — `httponly=True, secure=True,
  samesite="lax", path="/", max_age=30*24*3600, domain=settings.AUTH_COOKIE_DOMAIN or None`.
- Add `POST /api/auth/logout` that clears it. **There is no logout endpoint today** —
  logout is purely `localStorage.removeItem` — so this is new, and required, since a
  cookie cannot be cleared from JS.
- `backend/app/main.py` `jwt_auth_middleware`: when no `Authorization: Bearer` header is
  present, fall back to `request.cookies.get("gecko_session")`. Header keeps precedence,
  so Notes' own frontend is untouched.
- `.env.example` + `docker-compose.yml`: add `AUTH_COOKIE_DOMAIN` (`.geckopico.com` in
  prod; empty in dev yields a host-only cookie).
- **CSRF — the one real risk this introduces.** Today Notes needs no CSRF protection
  *because* auth is a header. A cookie is an ambient credential. Mitigation: for unsafe
  methods (POST/PATCH/DELETE) authenticated *by cookie rather than header*, require an
  `Origin` in the `CORS_ORIGIN` allowlist. `SameSite=Lax` plus the Origin check covers
  it, and Notes' own frontend never takes that path anyway.
- Tests: cookie authenticates; header wins over cookie; logout clears; cookie + foreign
  Origin on POST is rejected; `AUTH_COOKIE_DOMAIN` unset yields a host-only cookie.

**GN-2 — `GET /api/auth/session`.** Returns `{access_token, user}` when the cookie is
valid. GAM's SPA bootstraps from this instead of shipping a login form, and gets a raw
token for the WebSocket `?token=` pattern the cookie cannot serve.

**GN-3 — CORS and CSP widening (config + one nginx line).**
- `CORS_ORIGIN` → add `https://gam.geckopico.com`. Already comma-separated;
  `allow_credentials=True` is already set.
- `frontend/nginx.conf`: CSP `connect-src 'self'` → `'self' https://gam.geckopico.com`,
  and `img-src`/`media-src` likewise if Notes is to render GAM thumbnails inline.
  Without this the browser silently blocks every cross-app call.

**GN-4 — Asset-reference convention (closes an open question in the strategy doc).**
Use a plain link to `https://gam.geckopico.com/a/{assetId}`, **not** an `[asset:123]`
shortcode. BlockNote renders links already; a shortcode needs a custom block spec, a
parser, and handling in all six export paths (PDF/DOCX/MD/HTML/MP3/MP4). A custom
`geckoAsset` block is worth it only when inline previews are actually wanted — later,
and separately.

**GN-5 — Explicitly deferred.** Repointing Notes' media store at GAM (strategy doc
Phase 2) is *not* Phase 1. Notes keeps `./data/media/` and its `NoteAsset` bookkeeping;
GAM is additive. Migrating a live note corpus's media is its own project with its own
rollback story, and doing it before GAM has proven itself risks the app that currently
works.

**GN-6 — Unrelated bugs found while reading (offered, not required).**
- `backend/app/video/compose.py:22` points `FONT_DIR` at `backend/assets/fonts/`, which
  **does not exist in the repo** and is not copied by the Dockerfile (`COPY app/ ./app/`).
  Every video render today silently falls back to DejaVu instead of Inter. One-line fix
  (the fonts live at `backend/app/assets/fonts/`).
- `/media/*` being on the public-path allowlist means every upload in every private note
  is readable by anyone with the UUID URL. That may be intentional (it is what makes
  shared notes work), but it is worth an explicit decision rather than an inherited one.
- `AppConfig`/`configApi` is declared twice, in `frontend/src/api/notes.ts` (2 fields)
  and `frontend/src/api/config.ts` (6 fields).

---

## Milestones

Each is demoable on its own and ends in a draft PR on `claude/loving-mendel-x3xhnl`.

| # | Milestone | Contents |
|---|---|---|
| **M0** | **Genesis** | Repo skeleton mirroring gecko-notes; `Settings`; Alembic; `current_user` dependency; `/api/health`; Dockerfiles (backend needs **ffmpeg**), compose, nginx, CI (pytest + vitest + tsc, ffmpeg installed); ESLint/Prettier; `conftest.py` fixtures; React shell with the copied Tailwind/glass CSS layer. First commit. |
| **M1** | **Ingest & library** | `Storage` protocol + `LocalStorage`; streaming upload + drag-drop + bulk + paste (**URL import was specified here and never built** — see Outstanding below); ffprobe metadata; thumbnails — images (Pillow, copied), **video posters and PDF first-page previews (new)**; library grid + detail view; manual metadata editing; signed media URLs + Range/206. |
| **M2** | **SSO & first deploy** | GN-1/2/3 applied to gecko-notes; GAM verifies the JWT, shadow-user upsert, redirect-to-Notes login; `gam.geckopico.com` on the shared `web` network behind Caddy; restic backup sidecar. GAM is now genuinely usable. |
| **M3** | **Tagging** | Freeform tags, nested categories (recursive CTE), bulk apply/remove, filter chips, type/date/source/duration filters. |
| **M4** | **Transcription** | Deepgram prerecorded client; ffmpeg audio extraction; `EnrichmentJob` wired into the copied job runner; transcript viewer with click-to-seek; transcript editing (FR 7.1.3). |
| **M5** | **Search — the flagship** | FTS5 tables + sync; embedding provider + `embed` job + backfill; RRF hybrid ranking; results with snippet + timestamp + jump-to-moment. **Acceptance: the Giordano and Schwab queries from the SRS return the right asset at the right second.** |
| **M6** | **AI enrichment** | describe / summarize / autotag; suggestion accept-reject; `field_provenance` guarding manual edits; bulk enrichment over a selection; cost estimate before running + cumulative cost per asset and library. |
| **M7** | **Clips & sub-videos** | In/out point editor; non-destructive clips; ffmpeg extraction; parent-delete guard with promote-to-sub-video. |
| **M8** | **AI generation** | fal.ai image→image and image→video with multi-image bases; admin model catalog (`ModelCatalogEntry` pattern); generated assets through the standard ingest pipeline; regenerate-from-stored-prompt. |
| **M9** | **GVC surface** | Documented read API; embeddable `/picker` route with `postMessage`; GN-4 links from Notes. |

Phase 2 (out of scope, named so it is not accidentally designed out): cloud/R2 storage,
checksum dedup (the `checksum_sha256` column is already there for it), asset versioning,
shared libraries, library-wide background transcription, scene detection.

### Status

Kept current as milestones land, because this document is the handoff and the container
it was written in does not survive the session.

| Milestone | State |
|---|---|
| M0 Genesis | Merged — [#1](https://github.com/davior/gam/pull/1) |
| M1 Ingest & library | Merged — [#2](https://github.com/davior/gam/pull/2) |
| M4 Transcription | Merged — [#3](https://github.com/davior/gam/pull/3) |
| M2 SSO & first deploy | Merged — [#4](https://github.com/davior/gam/pull/4) |
| M5 Search | Merged — [#5](https://github.com/davior/gam/pull/5). Shipped unreachable; made configurable in #11, and reachable for a pre-existing library in #13 — **acceptance still not run, see below** |
| M3 Tagging | Merged — [#7](https://github.com/davior/gam/pull/7) (fast-forwarded, so no merge commit) |
| M6 AI enrichment | **Step 1 of 8 landed** — `AIProvider`, its migration, `/api/providers` CRUD and the settings panel, ported from gecko-notes. Steps 2–8 (provider clients, `UsageEvent`, describe/summarize/autotag, `field_provenance` enforcement, bulk enrichment) are specified in [`m6-ai-enrichment.md`](m6-ai-enrichment.md) and not started. |
| **M7–M9** | **Not started.** M7 is the only one with no external dependency. |

Non-milestone PRs, so a `git log` that does not match the table above still makes sense:
[#6](https://github.com/davior/gam/pull/6) moved dev ports to 8001/5174;
[#8](https://github.com/davior/gam/pull/8) added the startup warning for an unmigrated
database and the docs that go with it;
[#9](https://github.com/davior/gam/pull/9) documented `.env` for local dev and made the
CSP's Notes origin follow `NOTES_BASE_URL`.

### Outstanding, unscheduled

Found by auditing the code against this document rather than trusting it. None belongs to a
numbered milestone, and none is deliberate — they are here so a later session can tell a gap
from a decision, which is the distinction PR bodies do not preserve.

- **URL import (M1) was specified and never built.** No endpoint — though the SSRF guard
  it needs now exists as `backend/app/safe_url.py`, added for M6's provider base URLs and
  written to be reusable from here. The tell is `SOURCE_URL` in
  `ingest/filetypes.py`, declared with no writer — as are `SOURCE_AI` and `SOURCE_GVC`, which
  are legitimately waiting on M8 and M9. `FilterBar` offers an "AI generated" source filter,
  wired end to end and tested, over a value nothing can currently set.
- **No tag-management screen.** `tagsApi.create`/`recategorise`/`updateCategory` exist, and
  `rename`/`remove`/`createCategory`/`removeCategory` are wired into `stores/tags.ts` — all
  reachable from no component. A user can create a tag by typing it and can then never
  rename, delete or categorise it. The recursive-CTE category tree M3 built is, from the UI,
  read-only.
- **Search ignores `asset_type` and `limit`.** The backend accepts both
  (`routers/search.py:51-53`) and `api/search.ts` types them; `SearchView.tsx` passes
  neither. Results cap at the server default of 30 with no way to page or filter by type.
- **No `/a/{id}` deep link.** `assetsApi.get(id)` exists and is called by nothing, and there
  is no route. This is not cosmetic: **GN-4 specifies a Notes→GAM asset reference as a plain
  link to `/a/{assetId}`**, so that integration cannot work as designed until the route
  exists. The decision to use a link rather than a shortcode was taken partly *because* it
  needed no work in Notes — that reasoning assumed this end existed.
- **A 401 mid-session is a dead end.** There is no axios response interceptor; only
  `bootstrap()` handles 401, so an expiry during an upload or a search surfaces as an inline
  error string and nothing re-authenticates. Relatedly, `signOut()` exists in `stores/auth.ts`
  and no component calls it — there is no sign-out control anywhere in the UI.
- **The SRS is not in this repository.** M6–M8 are specified against FR numbers (8.1.3,
  9.1.4, 10.1.4, 11.1.1/2) that appear in this document and in code comments, in a source no
  session can read. Either commit it beside these docs or stop citing it; a requirement
  nobody can look up is not a requirement.
- **GN-7 and GN-8 are unapplied** in `davior/gecko-notes` — python-jose on five CVEs, and the
  Python version. Specified in `gecko-notes-integration.md`. Not changes to make from here.

Phase 2 items (cloud storage, checksum dedup, versioning, shared libraries, scene detection)
are listed above and are deliberate deferrals, not this.

### Carried by the user, not by code

These survive no session and are not blocked on anything in this repository. Each one is
a thing a future session will otherwise assume is done.

1. **M5's acceptance criterion has never been run, and until #11 it could not be.**
   This entry used to say the acceptance test merely awaited a real embedding key, which
   was wrong in a way worth recording: there was no way to supply one. `settings_store`
   defined `EMBEDDING_PROVIDER` and `OPENAI_API_KEY`, `build_embedder` read them, and
   nothing in between could write them — the settings router exposed Deepgram only. Nor
   did anything ever create an `embed` job: the worker's `KIND_EMBED` branch had no
   caller, so the vector table could only be filled by a test. Semantic search was off
   for every user, permanently and silently, while the search view told them to "add an
   embedding provider in Settings".

   #11 closed both halves, and #13 closed the one left behind it: nothing embedded the
   content that was *already* in the library, so a key added to a running instance only
   ever helped material transcribed afterwards. A library-wide backfill, a per-asset
   embed control and a global activity indicator now cover that.

   What remains is genuinely the user's: run the Giordano and Schwab queries against real
   content with a real key. The keyword half is verified end to end; the semantic half is
   exercised only by tests with stubbed retrievers, which prove the *fusion* is correct
   and say nothing about retrieval quality. Until that run happens, treat M5 as
   structurally complete and qualitatively unmeasured.
2. **GN-7 and GN-8 are specified and unapplied** (python-jose on five CVEs; the Python
   version). They are changes to `davior/gecko-notes`, not here — see
   `docs/gecko-notes-integration.md`.
3. **GAM does not yet consume GN-2.** The browser-side
   `GET {notes}/api/auth/session` that fetches the bearer token is not implemented in
   `frontend/src/api/`; authentication currently arrives on the `gecko_session` cookie
   alone. The frontend container's CSP already allows the call (`connect-src`, from
   `NOTES_BASE_URL`), so the CSP entry looks unused and is not — deleting it would pass
   every test today and silently break that fetch the day it is written.

M4 was brought forward past M2 and M3 because it is what M5 needs: there is nothing to
search until there are transcripts. M2 was deferred because it only decides *where* GAM
is reachable, and it was blocked on the gecko-notes changes landing (they since have, as
`95ed2ca` there).

Outstanding and outside the repo:

- **The M5 acceptance test is the user's to run.** It needs a real embedding key and real
  content; the sandbox has neither, and no test here is evidence about retrieval
  *quality* — see #5 for exactly which half is proven.
- A Deepgram key and an embedding provider go in through GAM's settings screen. Neither
  is ever shared with a session. The embedding half of that screen did not exist until
  #11 — if a future session finds a claim here that cannot be reached from the UI, that
  is the failure mode to check for first.

---

## Critical files

New, in `davior/gam` (branch `claude/loving-mendel-x3xhnl`):
`backend/app/config.py`, `backend/app/auth.py`, `backend/app/storage/local.py`,
`backend/app/models/asset.py`, `backend/app/search/{fts,vectors,hybrid}.py`,
`backend/app/enrichment/transcribe.py`, `backend/app/media/{clips,extract,ranged}.py`,
`backend/app/routers/{assets,search,enrichment,clips,activity}.py`,
`frontend/src/{api,stores,views,components}/`, `alembic/versions/`,
`docs/gecko-notes-integration.md`.

Copied from gecko-notes, fixed on the way in (paths are the sources):
`backend/app/jobs/{runner,registry}.py`, `backend/app/media_files.py`,
`backend/app/thumbnails.py`, `backend/app/routers/media.py` (`save_upload`,
`categorize_extension`, `sanitize_original_name`), `backend/app/asset_utils.py`
(`parse_media_url` — the path-traversal boundary), `backend/app/auth.py`
(`encrypt_api_key`/`decrypt_api_key`), `backend/app/pricing.py`,
`backend/app/routers/settings.py` (`_post_upstream`, `_require_safe_external_url`,
`compute_fal_cost`, `_record_usage`), `frontend/src/api/client.ts`,
`frontend/src/assets/{main,glass}.css`, `frontend/tailwind.config.js`,
`frontend/nginx.conf`, `frontend/Dockerfile`, `.github/workflows/ci.yml`, `ops/backup/`.

Modified in `davior/gecko-notes` (per `docs/gecko-notes-integration.md`):
`backend/app/routers/auth.py`, `backend/app/main.py`, `backend/app/config`/`.env.example`,
`docker-compose.yml`, `frontend/nginx.conf`.

Conventions to match: `{data: T}` / `ListResponse<T>` envelopes and
`detail: {code, message}` errors; JSON-as-TEXT columns; `str` uuid4 PKs generated at the
call site; types beside their api module (no `types/` dir); `@/` path alias; Zustand
without middleware, with the stale-response guard and `reset()`-on-logout fan-out.

---

## Verification

**Per milestone**
- `cd backend && pytest -q` — HTTP-level router tests (not just direct function calls),
  against the shared `conftest.py` app/client/tmp-storage fixtures.
- `cd frontend && npm test && npm run build` (`tsc --noEmit` is the typecheck gate).
- `docker compose up --build -d && curl -f http://localhost:<APP_PORT>/api/health`.
- Live dev: `uvicorn app.main:app --reload --port 8001` + `npm run dev` (8001/5174, not
  8000/5173 — gecko-notes holds those).

**M1** — upload a mixed batch (jpg, mp4, mp3, pdf) and confirm: every asset gets a
thumbnail or poster; `duration/resolution/codec` are probed; a signed URL plays and
*scrubs* (206 responses in devtools); an expired signature is rejected; a path-traversal
key is rejected.

**M2** — log into notes.geckopico.com, open gam.geckopico.com in the same browser,
land authenticated with no second login. Then: log out of Notes and confirm GAM
rejects; confirm a cross-origin POST with the cookie and a foreign `Origin` is refused;
confirm Notes' own frontend still works unchanged (it uses the header path).

**M5 — the acceptance test for the product.** Ingest a real long interview. Transcribe.
Then query *"the video where James Giordano was talking about deploying nano weapons"*
and *"a Klaus Schwab quote about the fourth industrial revolution"*, using wording that
does **not** appear verbatim in the transcript. Both must return the right asset with a
snippet and a timestamp that plays the right moment. Also verify FTS5 still wins on an
exact-phrase query, so the fusion is not just the vector index in disguise.

**M7** — cut a clip at 01:01–01:10; confirm no new file exists on disk and playback is
bounded. Extract the same range as a sub-video; confirm the file is standalone and the
parent is untouched. Attempt to delete a parent with clips and confirm the guard fires
and the promote path works.

**M8** — generate image→image from a base asset; confirm `ai_source_assets` records the
base, the prompt round-trips, regeneration works, and the cost recorded matches fal's
`x-fal-billable-units` header rather than an estimate.

**Environment note**: ffmpeg/ffprobe must be on `PATH` for M1 onward and in CI (the
gecko-notes workflow already installs it conditionally — copy that step). This
sandbox has neither ffmpeg nor sqlite3 installed, so media-touching work needs a dev
environment that does.

---

## Before M0 starts

Two prerequisites, both outside the code.

1. **Set `main` as the repository's default branch.** `davior/gam` was empty when this
   work began, so the first ref pushed became the default and GitHub still has
   `claude/loving-mendel-x3xhnl` in that role. `main` now exists at the same commit, but
   the default does not move on its own and cannot be changed through the GitHub tools
   available here. Change it at `github.com/davior/gam/settings` → General → Default
   branch. Until it is changed, every milestone PR will fail to find a base.

2. **Commit this plan into the repo** as `docs/plan-of-attack.md`, alongside
   `docs/gecko-notes-integration.md`. It currently lives only in the session's plan file
   and in the copy sent to the user. This is an ephemeral container — anything not
   pushed is lost when it is reclaimed — and a plan in the repo is what lets any later
   session pick the work up without re-deriving the research behind it.

### Session continuity

M0 should run in the session that produced this plan while that session is available:
the `gecko-notes` clone is already on disk at `/home/user/davior/gecko-notes`, and the
exploration behind every "copy this / fix this on the way in" decision is still in
context. A fresh session re-clones and re-derives it, and risks reading the gecko-notes
README — which is what produced the three wrong assumptions corrected at the top of this
document — rather than its source.

If a later session does pick this up cold, the two documents above are the handoff. The
one instruction that matters most: **verify claims about gecko-notes against its code,
not its README.**
