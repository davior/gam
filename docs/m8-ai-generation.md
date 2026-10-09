# M8 — AI generation

**Status:** built on `claude/keen-fermi-2rffhr` (backend and frontend in parallel,
integrated in one branch); not yet run against a live fal.ai host. This document was the
contract the two halves were built against, so the API section is exact rather than
illustrative. Where the build settled something the contract left open, or deliberately
did otherwise, the section concerned says so, and [As built](#as-built) collects every
such point in one place.

M8 adds three ways to make new media with fal.ai — **text → image**, **image → image**
from one or more base images, and **image → video** from a start frame (and, where the
model supports one, an end frame). Results are ordinary library assets with
`source = "ai_generated"`, carry enough provenance to be regenerated, and are costed
from fal's own billing header rather than an estimate.

Research behind this, kept out of the repository because it is long and dated: a read of
gecko-notes' fal code at `6c6f7ac`, a read of fal's published clients
(`@fal-ai/client` 1.11.0-alpha.5, `fal-client` 1.0.3 — the sandbox cannot reach any
fal.ai host, so the generated endpoint types in the JS client are the source for every
parameter name below), and a survey of GAM's own job, ingest, usage and settings code.

---

## What gecko-notes has, and what it does not

gecko-notes generates **text → image only**, synchronously (`POST https://fal.run/<id>`
inside the request), keeps nothing about a generation except the file and a usage row,
and "regenerate" re-sends the modal's in-memory state. There is no image → image, no
video, no multi-image input, no queue use, and nothing to port for
regenerate-from-stored-prompt. What *is* worth taking:

- the `Authorization: Key <key>` raw-httpx approach (fal's clients hide response
  headers, and the billing header is the point);
- `compute_fal_cost`'s shape — units from `x-fal-billable-units` × a per-endpoint unit
  price, `x-fal-request-id` as the external reference;
- `ModelCatalogEntry` — an admin-managed, global table of endpoints, because fal renames
  and retires endpoint ids every few months (`imagen4/preview`, all of `veo3/*` and
  `kling-video/v2.1` disappeared between May and October 2026). The catalogue must be
  data, not code.

What must **not** be copied: the price cache filled from fal's *usage* API (needs a
billing-scoped key, only knows endpoints already billed, and each refresh drops prices
outside its window); downloading the whole result into memory before checking its size;
reporting every fal error as a 502 carrying fal's raw JSON; a catalogue `PUT` that cannot
clear a field because it ignores `null`; and no uniqueness on the endpoint id.

## Decisions

### fal's queue API for all three kinds, polled from a job
`POST https://queue.fal.run/{endpoint_id}` returns `request_id`, `status_url`,
`response_url` and `cancel_url`; GAM stores all four **in the job payload, committed,
before it polls**. The synchronous endpoint is unsuitable for video (minutes long, no
retry, a dropped connection loses a result that is still billed) and two code paths for
one feature is one too many — an image simply completes on the first or second poll.

Persisting the request before polling is what makes restart safe: `recover_pending`
re-runs a job from the start, and a job that finds a stored `request_id` resumes polling
that request instead of submitting — and paying for — a second one. The same reasoning
applies after the download: created asset ids are written to the payload as each output
is ingested, so a restart after ingest finishes the job instead of duplicating assets,
and a `usage_recorded` flag keeps the cost from being recorded twice.

Status `COMPLETED` is also what a *failed* run reports, with `error`/`error_type` set, so
those are checked before fetching the result. Unknown statuses are non-terminal; GAM's
own deadline (`generation_timeout_minutes`) bounds the wait, and on timeout or user
cancel GAM sends `PUT {cancel_url}` (best effort) — a remote job left running keeps
billing.

Webhooks were rejected: GAM sits behind a reverse proxy with no public callback URL, and
polling from a worker thread needs nothing inbound.

### Generation gets its own worker queue
Every job shares one `JobQueue` at `enrichment_concurrency = 1`. A video that takes four
minutes would hold every transcription queued behind it. `JobQueue` gains a kind filter
(`kinds` / `exclude_kinds`), applied in `recover_pending` and `sweep_stale` so two queues
over the same `EnrichmentJob` table never recover or sweep each other's rows. The
enrichment queue excludes `generate`; a second queue owns it at
`generation_concurrency = 2`. A generation is almost entirely waiting on fal, so two at
once costs the container nothing.

### Base images go to fal as data URIs
GAM's media is not public and must not become public to serve this. fal's two options
are a `data:` URI in the request body or an upload to fal's CDN. Data URIs win:
- nothing of the user's lands on a public CDN with no expiry (gecko-notes' audio uploads
  do exactly that);
- no second, separately-versioned upload API (fal has already broken the older
  `storage_type=gcs` flow once).

The cost is body size, so each base is normalised first: EXIF orientation applied, long
edge capped at `generation_base_max_edge` (2048 px), re-encoded as JPEG (PNG when it has
transparency), and refused if the result still exceeds `generation_max_data_uri_mb`
(8 MB). Bases must be image assets that own a file and that Pillow can read — not clips,
not avif/heic/svg. **Unverified:** that every seeded model accepts data URIs; fal's
schemas say "publicly accessible or base64 data URI". If one does not, CDN upload is the
fallback, not a redesign.

### The catalogue describes each endpoint's dialect
Endpoints disagree on everything: the image field (`image_url`, `image_urls[]`,
`start_image_url`), the end-frame field (`tail_image_url`, `end_image_url`,
`last_frame_url`, or none), the type of `duration` (`"5"`, `"8s"`, `6`), and which of
`aspect_ratio` / `image_size` / `resolution` / `seed` / `negative_prompt` exist at all.
Rather than code per model, each catalogue row carries:

- `image_field`, `image_field_is_list`, `max_images`, `end_image_field`;
- `options` — the *exact values* the endpoint accepts for each user-facing choice
  (`aspect_ratios`, `image_sizes`, `durations`, `resolutions`), passed through verbatim so
  `"5"` and `"8s"` and `6` all work, plus `supports_seed`, `supports_negative_prompt`,
  `supports_audio` and `max_outputs`;
- `extra_params` — defaults merged into every request body first (e.g.
  `{"generate_audio": false}` for Veo, whose audio default raises the price by half).

The form offers only what a row declares, and the server rejects a value outside the
declared list. This also closes gecko-notes' worst catalogue bug: a seeded "text to
image" model that was really an edit endpoint and 422'd every call.

### Admins are named in config
`User.is_admin` exists and `/api/me` returns it, but nothing sets it and the Notes token
carries no admin claim. `ADMIN_USERS` (comma-separated user ids or usernames, both matched
exactly — Notes usernames are unique only case-sensitively and anyone can rename, so
ids are the recommended entry) makes a user an admin; `/api/me` reports
`is_admin = record.is_admin or listed`. An `AdminUser` dependency answers
`403 {"code": "admin_required"}`. Only admins edit the catalogue; everyone uses it.
With `ADMIN_USERS` empty nobody can edit it, and the seeded catalogue still works.

### Provenance lives on the asset; the base is not a parent
`parent_asset_id` is wrong for a base image: anything with a parent inherits its
attribution (M10), appears in the parent's Clip tab, and one column cannot hold several
bases. New nullable columns on `Asset`:

| column | holds |
|---|---|
| `ai_model` | the endpoint id |
| `ai_generation_type` | `text_to_image` / `image_to_image` / `image_to_video` |
| `ai_prompt` | the prompt as sent |
| `ai_parameters` | JSON — the request body exactly as sent, minus `prompt` and the image fields (catalogue defaults included, so a regeneration is reproducible after the catalogue changes). Keys are fal's: the number of outputs is `num_images`, present only for an image row whose `max_outputs` is above 1 |
| `ai_source_assets` | JSON list of `{"asset_id", "role": "base" \| "end_frame", "name"}` — `name` is a snapshot so a deleted base still reads as something |
| `ai_seed` | the seed fal reports back, when it reports one |
| `ai_generated_at` | naive UTC, from `app.clock.utcnow` |

Bases are referenced by id inside JSON, not by foreign key: deleting a base never blocks
or breaks a generated asset; it only makes regenerating it impossible, which is reported
as such.

The generated asset's `name` is the prompt cut at a word boundary (≤ 80 chars), and its
`description` is the full prompt, both stamped `"ai"` in `field_provenance`. That makes a
generation findable by what was asked for — keyword and semantic search both read
`description` — without an FTS rebuild migration. A later Describe run overwrites the
description as it would any other; `ai_prompt` keeps the original.

### Exact cost
fal's `x-fal-billable-units` (on the queue **result** fetch, with `x-fal-request-id`)
gives the billed quantity in the endpoint's unit. The unit price comes from fal's
pricing API, `GET https://api.fal.ai/v1/models/pricing?endpoint_id=…` with the user's key,
cached in-process per (user, endpoint) for six hours.

| units from | price from | `cost` | `cost_estimated` |
|---|---|---|---|
| header | pricing API | units × price | `False` — the exact bill |
| header | catalogue `unit_price` | units × price | `True` |
| outputs (header absent) | either | quantity derived from what was downloaded (images, megapixels rounded up per image, probed seconds, videos) × price | `True` |
| — | no price anywhere | `None` | `None` — "not priced" |

**Unverified:** that the queue result carries the billing header (one third-party source
says it does; gecko-notes only ever read it from the synchronous endpoint), and that the
pricing API accepts an ordinary key. Both degrade to the estimated rows above rather than
to nothing. The first real generation answers both questions.

One `UsageEvent` per fal request, attached to the first output asset, with
`provider="fal.ai"`, `model=<endpoint id>`, `kind="image"` or `"video"`, and fal's
request id in a new `external_ref` column. `unit_type` is `images`, `megapixels`,
`generated_seconds` or `videos` — deliberately not `seconds`, which the usage totals
already sum as transcribed audio. `units` is an integer column, so it holds the billed
quantity rounded up; `cost` is computed from the exact value first.

### A generated video is not auto-transcribed
`_chain_transcription` would send every fal video to Deepgram and bill for it, and most
generated video is silent or music. Ingest skips it for `SOURCE_AI`; the Transcribe
button still works by hand.

---

## Schema (one Alembic revision)

1. `asset`: the seven `ai_*` columns above, all nullable, no indexes, no foreign keys.
2. `usageevent`: `external_ref` (nullable string).
3. `generationmodel` (new):

| column | type | notes |
|---|---|---|
| `id` | str PK | uuid4 |
| `endpoint_id` | str | **unique** index |
| `kind` | str | indexed; `text_to_image` / `image_to_image` / `image_to_video` |
| `label` | str | |
| `note` | str | default `""` |
| `sort_order` | int | default 0 |
| `is_active` | bool | default true |
| `image_field` | str? | null for text → image |
| `image_field_is_list` | bool | default false |
| `max_images` | int | 0 for text → image, ≥ 1 otherwise |
| `end_image_field` | str? | image → video only |
| `options` | str | JSON, default `"{}"` |
| `extra_params` | str | JSON, default `"{}"` |
| `unit_price` | float? | list price, the fallback when the pricing API is unavailable |
| `price_unit` | str? | `image` / `megapixel` / `second` / `video` |
| `price_currency` | str? | |
| `created_at`, `updated_at` | datetime | naive UTC |

4. Seed rows, inserted by the same revision, three per kind. Every parameter name and
   allowed value is checked against the endpoint types in fal's client before seeding:
   - text → image: `fal-ai/flux/schnell`, `fal-ai/flux/dev`, `fal-ai/nano-banana-2`
   - image → image: `fal-ai/nano-banana/edit`, `fal-ai/bytedance/seedream/v4.5/edit`
     (≤ 10 bases), `fal-ai/flux-pro/kontext` (one base)
   - image → video: `fal-ai/kling-video/v2.5-turbo/pro/image-to-video` (start + end
     frame), `fal-ai/minimax/hailuo-02/standard/image-to-video` (start + end, cheap),
     `fal-ai/veo3.1/fast/image-to-video` (`generate_audio: false` by default)

---

## API

Envelopes and errors follow the house contract (`{"data": …}`,
`{"detail": {"code", "message"}}`).

### Catalogue
`GenerationModelRead`:
```json
{"id": "…", "endpoint_id": "fal-ai/flux/dev", "kind": "text_to_image",
 "label": "FLUX.1 [dev]", "note": "", "sort_order": 10, "is_active": true,
 "image_field": null, "image_field_is_list": false, "max_images": 0,
 "end_image_field": null,
 "options": {"aspect_ratios": [], "image_sizes": ["square_hd", "landscape_16_9"],
             "durations": [], "resolutions": [], "supports_seed": true,
             "supports_negative_prompt": false, "supports_audio": false,
             "max_outputs": 4},
 "extra_params": {}, "unit_price": 0.025, "price_unit": "megapixel",
 "price_currency": "USD", "created_at": "…", "updated_at": "…"}
```
- `GET /api/generate/models?include_inactive=false` → list envelope, ordered by
  `kind, sort_order, label`. `include_inactive` is honoured for admins only.
- `POST /api/generate/models` *(admin)* → `201`, `GenerationModelRead`.
  `409 endpoint_exists` on a duplicate `endpoint_id`.
- `PATCH /api/generate/models/{id}` *(admin)* → updates exactly the fields present in
  the body; an explicit `null` clears a nullable field. `null` on `note`, `options` or
  `extra_params` resets it to its default (`""`, `{}`, `{}`); `null` on any other
  non-nullable field is `422 invalid_model_entry`. The merged row is validated as a
  whole, so changing `kind` alone can be refused for a field it leaves behind.
- `DELETE /api/generate/models/{id}` *(admin)* → `204`.
- Validation (`422 invalid_model_entry`): `kind` in the three values; `max_images` 0 and
  no `image_field` for text → image, `max_images ≥ 1` and an `image_field` otherwise;
  `end_image_field` only for image → video; `max_outputs` 1–4 (1 for video); `options`
  and `extra_params` are objects. As built, also: `endpoint_id` must look like a fal id
  (two or more `/`-separated segments of `[A-Za-z0-9._-]`, so no `..` or query string,
  since it becomes a URL path); `image_field` / `end_image_field` must be plain request
  field names; `max_images > 1` requires `image_field_is_list`; `options` refuses unknown
  keys (a typo such as `duration` would otherwise save and do nothing), its lists hold
  only strings or numbers and its flags only booleans; `price_unit` is one of
  `image` / `megapixel` / `second` / `video`; `unit_price` is not negative. The admin
  form repeats the rules a person is likely to trip over before sending.

### The fal key
`GET /api/settings/generation` → `{"data": {"fal_key_configured": bool}}`.
`PUT /api/settings/generation` with `{"fal_api_key": "…"}` stores it encrypted (setting
key `fal_api_key`, in `settings_store.SECRET_KEYS`); `""` removes it; omitted leaves it
alone. The key is never returned, masked or otherwise.

### Generating
`POST /api/generate` → `202 {"data": ActivityJobRead}`
```json
{"model_id": "…", "prompt": "…",
 "base_asset_ids": ["…"], "end_frame_asset_id": null,
 "params": {"aspect_ratio": null, "image_size": null, "duration": null,
            "resolution": null, "seed": null, "negative_prompt": null,
            "generate_audio": null, "num_outputs": 1}}
```
Checked at submit time, in this order:
- `400 fal_key_missing` — no key stored.
- `404 model_not_found`; `409 model_inactive`.
- `422 invalid_generation` with a specific message, for:
  - an empty prompt or one over 4,000 characters;
  - the wrong number of bases for the kind (none for text → image, 1–`max_images` for
    image → image, exactly one for image → video);
  - an end frame on a model without `end_image_field`;
  - a choice outside the row's declared list;
  - `seed` / `negative_prompt` / `generate_audio` on a model that does not declare it;
  - `num_outputs` outside 1–`max_outputs`.
- `404 asset_not_found` for a base the user does not own.
- `422 invalid_base` for a base that is not an image, owns no file, or is not readable.

The job's `asset_name` is the prompt, truncated. `ActivityJobRead` gains
`result_asset_ids: list[str]` (every created asset, in output order); `result_asset_id`
stays the first.

`POST /api/generate/{asset_id}/regenerate` → `202 {"data": ActivityJobRead}`, body
`{"prompt": null, "reuse_seed": false}`. Re-submits the stored endpoint, parameters and
sources (optionally with a new prompt), through the endpoint's *current* catalogue row
for its field names and row id. The stored parameters are not re-checked against the
row's current option lists. A stored `seed` is dropped, so plain Regenerate gets a new
picture; with `reuse_seed`, and a row that supports seeds, `seed` is `ai_seed` — or, for
an endpoint that reports none back, the `seed` the request originally asked for.
In this order:
- `404 asset_not_found` when the asset is not owned.
- `409 not_generated` when the asset has no stored generation.
- `400 fal_key_missing` — no key stored.
- `409 model_unavailable` when no active catalogue row has that endpoint id, or the
  active row is now of a different kind.
- `409 source_asset_missing` when a base was deleted, naming it.
- `422 invalid_generation` for a new prompt that is empty or too long, or stored bases
  that no longer fit the row; `422 invalid_base` for a base that can no longer be read.

### Asset read model
`AssetRead` gains `generation` — `null`, or:
```json
{"model": "fal-ai/flux/dev", "kind": "text_to_image", "prompt": "…",
 "parameters": {…}, "sources": [{"asset_id": "…", "role": "base", "name": "…"}],
 "seed": 1234, "generated_at": "…"}
```
`seed` and `generated_at` are nullable: the columns are read leniently, so a row edited
by hand reads with gaps rather than failing the whole asset.

### Activity
Action `generate`. Stages, in order: `Preparing the base images`, `Submitting to fal.ai`,
`Waiting in fal.ai's queue` (detail: queue position), `Generating`, `Downloading`,
`Saving`, `Recording the cost`. Errors are sentences a user can act on —
`fal.ai rejected the API key — check it in Settings`, `fal.ai refused the request:
image_urls: field required`, `fal.ai did not finish within 30 minutes`, `A base image
was deleted before this could run`, `The generated file is larger than the 1024 MB limit`.

---

## Configuration (`app/config.py`)

| setting | default | |
|---|---|---|
| `admin_users` | `""` | comma-separated user ids or usernames |
| `fal_queue_base_url` | `https://queue.fal.run` | |
| `fal_api_base_url` | `https://api.fal.ai` | pricing |
| `generation_concurrency` | `2` | the generation queue's workers |
| `generation_timeout_minutes` | `30` | then cancel at fal and fail |
| `generation_poll_seconds` | `3` | |
| `generation_max_download_mb` | `1024` | per output, enforced while streaming |
| `generation_base_max_edge` | `2048` | base image long edge, px |
| `generation_max_data_uri_mb` | `8` | per base, after normalising |

Result downloads stream to a temp file through a new capped helper: SSRF check
(`safe_url.require_safe_external_url`) on the URL and on each redirect hop (at most
three), `Content-Length` checked up front, bytes counted while streaming, the job's
progress callback called between chunks so a cancel lands mid-download. The body is
requested and accepted only uncompressed — httpx inflates gzip and brotli with no output
limit, before a byte could be counted — and the whole file must arrive within
`generation_timeout_minutes`, so a server that trickles bytes cannot hold a worker
forever. A 429, a 5xx or a dropped connection is tried three times before it fails the
job: fal has already billed by then and keeps the file for days. The extension comes
from the URL path when it is a known media type, else from `Content-Type`, else
`.png` / `.mp4` by kind.

## Frontend

- `api/generate.ts` — types beside it, `generateApi = { models, createModel, updateModel,
  deleteModel, start, regenerate }`. `api/settings.ts` gains `generationSettingsApi`.
  `Asset` gains `generation`; `User` gains `is_admin`.
- `stores/generation.ts` — the catalogue, loaded once, with `reset()` fanned out on
  logout.
- `components/GenerateForm.tsx` — one form, three entry points. Kind is chosen from what
  the bases allow (none → text → image; one or more → image → image, or image → video
  with the second base as the end frame when the model has one). The model list is
  filtered by kind, with its note and list price ("≈ $0.025 per megapixel, list price").
  Every option control renders only when the selected model declares it. Submitting
  queues the job and says to follow it under background activity, as `UrlImport` does;
  `fal_key_missing` says to add a key in Settings.
- Entry points: a third section in `AddPanel` (text → image); "Generate from these" in
  `SelectionBar` when every selected asset is an image that owns a file; and a
  **Generate** tab in `AssetDetail` for any image asset with a file and any asset with a
  `generation`. On a generated asset that tab leads with the provenance (model, prompt,
  parameters, sources linked to `/a/{id}`, seed, cost) and **Regenerate**,
  **Regenerate with the same seed** (when there is one) and **Edit and generate**, which
  opens the form pre-filled.
- `LibraryView` adds every `result_asset_ids` entry of a finished `generate` job to the
  grid, the way it does an import. `ActivityIndicator` labels it "Generating".
- Settings: a **Generation** panel with the fal key (the `SpeechPanel` pattern) and the
  model catalogue — editable for admins, read-only for everyone else.
- The asset Info tab's "AI cost (est.)" and `UsagePanel`'s "an estimate, not a bill"
  respect `estimated`, now that an exact cost exists.

## Known limitations at the outset

- No picker for bases beyond "what is selected" and "this asset" — the embeddable picker
  is M9.
- A video's frame cannot be a base; only image assets can.
- Generated outputs keep fal's copy for fal's default retention (≥ 7 days). Deleting it
  sooner needs fal's payload-deletion API and a billing-scoped key.
- Cost is reconciled against nothing after the fact; fal's billing-events API would give
  the discounted final amount and is the natural follow-up.
- A *running* job cancelled through the API whose process dies before the worker reaches
  its next checkpoint never sends `PUT {cancel_url}` — `recover_pending` skips cancelled
  rows — so that fal request runs to completion. (A *queued* one is cancelled at fal by
  the cancel itself; see the review fixes below.)
- An admin who adds `sync_mode` to a row's `extra_params` gets results back as inline
  `data:` URIs, which are accepted but then sit in the job payload, megabytes and all.
- A generation started from an asset's own page (`/a/{id}`) is not added to any grid,
  since only `LibraryView` watches jobs; its outputs appear on the next library load, as
  a URL import's already do.

---

## As built

What the build settled that the contract above left open, or did differently on
purpose. The API sections above have been brought into line with each.

**Backend**
- Catalogue validation is stricter than the original list, and `PATCH` with `null` on a
  non-nullable field resets or refuses (both now in the Catalogue section).
- A body field of the wrong *type* (`"max_images": "abc"`, `"seed": "x"`) gets FastAPI's
  own 422 list, not the house `{code, message}`, as everywhere else in the app. A
  missing `endpoint_id` / `kind` / `label`, or an empty or missing `prompt` / `model_id`,
  does get the house code, because those fields are optional in the schemas and checked
  by hand.
- Plain Regenerate drops the stored seed; Regenerate also answers `fal_key_missing`,
  `invalid_generation` and `invalid_base` (now in the Generating section).
- `num_images` is sent, and stored, only for image rows that declare `max_outputs > 1`;
  a video request never carries it.
- The job's `asset_name` and the asset's `name` are the prompt cut at a word boundary to
  at most 80 characters, ending in `…` when cut.
- A fal 401/403 whose body mentions a balance is reported in fal's own words
  (`fal.ai refused the request: …`) rather than as a bad key, so an exhausted balance
  does not send the user off to re-check a key that is fine.
- A status check that fails transiently (429, 5xx, a dropped connection) is retried until
  the deadline rather than failing the job. The queue detail reads `Next in line` or
  `Position N in the queue`.
- `estimated` in the usage totals is now true when *any* priced row is an estimate (it
  was `all()`, which called a total mixing a bill and an estimate exact). The asset's
  "AI cost (est.)" and the usage panel's caveat follow it.

**Frontend**
- The four option lists, and the matching `params`, are `string | number` end to end, so
  a value goes back exactly as the row declared it.
- Edit and generate reads the number of outputs from `parameters.num_images` and fetches
  each source first, naming one that has gone instead of leaving it to
  `source_asset_missing`; plain Regenerate shows the server's message as it is.
- "Regenerate with the same seed" shows when there is a seed to send — `seed`, or the
  `parameters.seed` the server falls back to — and the active row does not declare
  `supports_seed: false`.
- An admin's edit sends every field in the `PATCH`, with `null` for the fields the kind
  cannot use, so switching a row's kind clears what no longer fits; toggling active sends
  only `is_active`.
- Whether an asset can be a base is checked in the browser by type and file alone (an
  image, not a clip, not missing, with a `file_url`); the avif/heic/svg refusal is the
  server's `invalid_base`.
- The provenance block's Cost is the asset's `/api/usage/assets/{id}` total, marked
  `(est.)` while it is one.

**Fixed after an adversarial review**, each with a test that fails without it:
- A request fal has billed always gets its `UsageEvent`, even when saving its outputs
  fails or is cancelled part-way; it is attached to the first output that was saved, if
  any. Before, the cost was recorded only after every output was saved, and an errored
  job is never run again to record it later.
- A download that fails with a 429, a 5xx or a dropped connection is retried twice
  before it fails the job; before, one CDN hiccup cost a second generation.
- A 5xx on the result fetch that carries fal's `x-fal-request-id` is the model's own
  failure, and now fails the job with fal's message. It used to be polled until the
  thirty-minute deadline, holding a worker, and then reported as "did not finish".
- A restart in the middle of `ingest_file` — which commits the row before probing and
  thumbnailing — no longer leaves a provenance-less copy beside the finished one. The
  index being saved is written to the payload first; a resumed run that finds it set
  discards this user's generated assets, made since the job was queued, with the same
  bytes, then saves the output again.
- Cancelling a *queued* generation that already has a request at fal — one recovered
  after a restart, waiting for a worker — now sends `PUT {cancel_url}`. No worker ever
  reaches the checkpoint that would.
- `ADMIN_USERS` matches usernames exactly. Notes keeps usernames unique only
  case-sensitively and lets anyone rename, so case-insensitive matching made whoever
  renamed to `DAVIOR` an admin alongside `davior` — and an admin chooses the endpoint
  every user's prompts and base images are sent to. Ids are now the recommended entry.
- The download refuses a compressed body and has a whole-file deadline (see
  Configuration).
- The check that keeps the fal key on fal's own queue host had no test; removing it left
  the suite green. Five variants of a foreign queue URL are now tested.
- The form's "Generate audio" box sends what it shows. Untouched, it used to send `null`,
  leaving audio to an endpoint whose default is on, for any row an admin added without
  pinning `generate_audio`.
- The library grid adds the outputs a generation saved before it failed or was
  cancelled; it used to wait for `done`.

**Still unverified**, because no fal host was reachable from the build: whether the
queue result carries `x-fal-billable-units`, whether the pricing API accepts an ordinary
key, and whether every seeded model accepts data-URI bases. gecko-notes' own comment that
fal rejects `data:` URIs for *audio* inputs makes the third a real risk; CDN upload is
still the fallback. The first real generation answers all three.
