# M7 — Clips & sub-videos

**Status:** complete. Non-destructive clips, destructive sub-video extraction (fast
stream-copy with an automatic re-encode fallback), and a parent-delete guard with a
one-click promote path are all built, backed by real ffmpeg-verified tests (this
sandbox had none at first; `apt-get install ffmpeg` got real coverage instead of relying
on the `needs_ffmpeg` skip marker, and it caught two test bugs the skip would have
hidden — see "What real ffmpeg caught" below).

Two research passes preceded the code: a read-only survey of the actual codebase (not
`docs/plan-of-attack.md`'s aspirational file sketch, which turned out to be stale in
three places — see "Where this diverges from the plan doc" below), and an independent
validation pass that pushed back on parts of the design. Both are folded into this
document rather than kept separate, so the reasoning survives in one place.

---

## Where this diverges from the plan doc

`docs/plan-of-attack.md`'s architecture sketch names `backend/app/media/{clips,extract,
ranged}.py` for this milestone. None of that held up against the actual code:

- **`services/assets.py`'s own module docstring already claims this work**: *"an
  extracted sub-video (M7) ... land[s] here"*. Clip and sub-video Asset-row lifecycle
  (`create_clip`, `list_children`, `blocking_clips`, `create_subvideo_asset`,
  `promote_clip`) lives in that existing module, not a new one — it already had the
  hook waiting.
- **`media/` isn't where ffmpeg-touching jobs live.** Every existing one —
  `transcribe.py` for `KIND_TRANSCRIBE`, `extract_text.py` for `KIND_EXTRACT_TEXT` — is
  a `enrichment/<kind>.py` orchestration file (DB-aware, called from
  `jobs/enrichment.py`'s dispatch), paired with a sibling mechanics-only file with no DB
  knowledge when ffmpeg is involved (`transcribe.py` → `audio.py`). `media/` holds only
  `ranged.py` (HTTP range-response streaming), a different kind of code entirely. M7
  follows the established pattern: `enrichment/subvideo.py` (mechanics) +
  `enrichment/extract_subvideo.py` (orchestration) — no new `media/` files at all.
- **`routers/clips.py` is the one part of the sketch that held up as named.**

## Design decisions

### A clip is a `source`, not an `asset_type`

`Asset.asset_type` stays the parent's real media type (`video`/`audio`) — the existing
`Preview` switch, `ASSET_TYPES` validation, and type filters needed zero changes. What
identifies a clip *physically* is `storage_key IS NULL AND parent_asset_id IS NOT NULL`,
exactly as the model's own docstring already framed it before this milestone touched it.
Two new values went into `ingest/filetypes.py`'s **source** constants instead —
`SOURCE_CLIP = "clip"`, `SOURCE_SUBVIDEO = "sub_video"` — safe because `source`, unlike
`asset_type`, was never validated against a frozenset.

`source` doubles as the frontend-facing signal for "does this row own bytes" —
`AssetRead`/`Asset` already exposed `source`, so nothing new had to be added there. An
earlier draft of this design added a computed `is_clip: bool`; dropped in favour of the
field already being written in the two places (`create_clip`, `promote_clip`) that also
touch `storage_key`, so it can't independently drift.

Checked directly, not assumed: every `asset_type` switch in the codebase
(`enrichment/source.py`, `transcribe.py::can_transcribe`, `describe.py`,
`extract_text.py::extractable`) already also gates on `storage_key`/`thumb_key` being
non-null, so a clip falls through to a clean 400 everywhere that matters, never a crash.
Search/FTS/tags switch on nothing asset-type-related at all — a clip indexes and is
found exactly like any other asset once `create_clip` calls `_reindex`.

### The blocking query is narrower than "has a parent"

Only children with `parent_asset_id = X AND storage_key IS NULL` block deleting `X` — a
promoted clip or a freshly-extracted sub-video has its own `storage_key`, so even though
it keeps `parent_asset_id` as a provenance breadcrumb, it must not block the parent's
delete or appear in `blocking_clips`. This stays the physical, DB-level check (not the
`source` label) — it's what a delete actually depends on being true.

`delete_asset` proactively nulls out `parent_asset_id` on *non-blocking* children before
deleting the parent (`services/assets.py::delete_asset`) — otherwise the real foreign
key (`PRAGMA foreign_keys=ON` on every connection, matching production) raises on them
too, the same reason `AssetTag`/`Suggestion` rows are already cleared first. This only
manifests once the promote flow is actually used, which is exactly the kind of gap that
ships clean and breaks in the field — it's covered by
`test_delete_after_promoting_succeeds`-shaped tests in `test_clips_api.py`.

For the constraint to exist at all, the migration needed an explicit
`batch_op.create_foreign_key(...)`, not just `add_column` — a bare nullable column gets
SQLModel's ORM-level awareness of the relationship but not SQLite's actual enforcement
of it, which is what the guard-before-delete design depends on. No prior migration in
this repo added a foreign key to an *already-existing* table (both of the other two —
`assettag`, `suggestion` — got theirs at `create_table` time), so this is a new pattern
here, verified against a real SQLite database (`PRAGMA foreign_key_list(asset)`), not
just against the model.

### `gather()` needed a clip guard M6 never anticipated

`enrichment/source.py::gather()` is the one chokepoint describe/summarize/autotag/
generate_all all read through. Because a clip carries its parent's `thumb_key` (copied
directly at creation — see below), `gather()`'s poster-image fallback would otherwise
silently succeed on a clip using a generic frame — never the parent's transcript for
that exact time range — producing a description of the wrong content rather than
failing loudly. One guard clause at the top of `gather()` closes this for all four jobs
at once, using the `NoSourceMaterial` exception type and cascade handling that already
existed. This is defense-in-depth even where a per-job pre-check
(`can_transcribe`/`extractable`-style) might already 400 the common button-click path —
it's the one place all four jobs funnel through regardless.

The frontend independently stops *offering* these actions on a clip (`source ===
'clip'` in `AssetDetail.tsx`) — the backend 400s/refuses cleanly either way, but a UI
that shows four buttons which all fail immediately is a real defect even though nothing
crashes. Gated on `source`, not on `parent_asset_id` being set — a promoted or
freshly-extracted sub-video also carries `parent_asset_id` as a breadcrumb but owns
real bytes and keeps every one of these actions.

### Two backend flows, not one

The acceptance test exercises them separately, and they stay separate in the API:

- **Fresh extraction** (`POST /assets/{id}/subvideo`) creates a brand-new standalone
  Asset only once the job succeeds — no placeholder row, parent genuinely untouched.
- **Non-destructive clip** (`POST /assets/{id}/clips`) is fully synchronous, no ffmpeg,
  no job — an Asset insert and nothing else. 201, not 202.
- **Promote** (`POST /assets/{clip_id}/promote`) reuses the same ffmpeg job but updates
  the *existing clip row in place* at completion (`storage_key` set, `source` flips
  `clip`→`sub_video`, `in_point`/`out_point` cleared — see the bug below), so nothing
  that already references that asset id breaks.

One new job kind, `KIND_EXTRACT_SUBVIDEO`, deliberately not added to `ENRICHMENT_KINDS`
(that gates M6's AI-cost bulk-enrichment UI; this has no AI cost, the same reason
`KIND_EXTRACT_TEXT` sits apart from the LLM jobs) but dispatched through the same
`EnrichmentJob` table/`_run_job` chain everything else uses. Its payload carries
`{"mode": "extract"|"promote", "in_point", "out_point", "name"?}` going in; on
completion, `jobs/enrichment.py`'s dispatch overwrites it to
`{"created_asset_id": ...}` for "extract" mode only, which is what lets
`ActivityJobRead.result_asset_id` (new field, populated in `jobs/registry.py` from a
best-effort JSON parse, mirroring `transcripts.py::_words_of`) tell the frontend what
was created — `job.asset_id` stays pointed at the *source* throughout an extract job's
life, so without this the frontend would have no way to learn the new asset's id at all.

No aggregate "promote all" job kind — the frontend loops, firing one promote call per
dependent clip (typically a handful), polling `GET /api/activity/{kind}/{job_id}`
(added to `activityApi` — the existing `list` endpoint's default 25-row cap and the
activity store's own capped `jobs` array are both real limits when waiting on several
specific ids at once) to a terminal state for each. Only once *every* promote succeeds
does the frontend retry the delete; a failure surfaces and stops rather than retrying
into another guard or dropping a clip silently.

### A bug `promote_clip` almost shipped with

`in_point`/`out_point` are **cleared**, not preserved, when a clip is promoted. The
first draft kept them "for provenance" — wrong: they're coordinates into the *parent's*
timeline, and the freshly extracted file has its own timeline starting at 0. Leaving
the old values in place would have `AssetDetail`'s playback-bounding logic (which seeks
to `asset.in_point` and pauses at `asset.out_point` for any asset that has them) seek a
nine-second standalone file to its old absolute second 61 the moment it was promoted.
Caught by re-deriving the logic during implementation, not by a test — worth naming
because it's exactly the kind of thing a future change to either promote or the player
could reintroduce without one.

### A second bug: the delete guard's own remount

`stores/library.ts`'s `remove()` optimistically drops the asset from the library
store's `assets` array *before* the API call resolves, then restores it if the call
fails. `AssetView.tsx` reads its asset via `assets.find(...)` and renders `null` when
that comes back empty — so calling `remove()` for an asset that turns out to be guarded
briefly unmounts `AssetDetail` entirely, and remounts a *fresh* instance once the store
puts the asset back. That's invisible for a plain failure (resetting `confirmingDelete`
to `false` is what a fresh mount does anyway), but the first implementation of the
promote-guard UI called `setDependentClips(...)` from inside `remove()`'s `catch` —
state set on a component instance that had already been replaced by the time it ran,
so it silently did nothing. Three new frontend tests failed against this before it was
diagnosed and fixed.

The fix is architectural, not a patch: `confirmDelete` now checks `clipsApi.list` for
blocking clips *before* ever calling `remove()`, rather than reacting to a 409 from it.
The guarded path never triggers the optimistic-removal dance at all. The backend's own
`blocking_clips` check in `routers/assets.py::delete_asset` stays the authoritative
enforcement — this is a client-side UX fix, not a replacement for it; a race (a clip
created between the check and the delete, vanishingly unlikely for anything but a
multi-tab session) falls back to the plain "could not delete" path rather than the
promote UI, which is an acceptable edge case for something this rare.

### The ffmpeg fallback heuristic, verified against real output

`enrichment/subvideo.py`'s two strategies: input-side seek + stream copy
(`-ss` before `-i`, `-c copy`) is fast but can only start on a keyframe, so a cut
between keyframes overshoots — ffmpeg targets `in_point + duration` in the source's
timeline but has to start at the nearest keyframe at or before `in_point`, so the actual
output spans further back than requested. The fallback (output-side seek, always
re-encodes to h264/aac) is slower but frame-accurate. The output's duration (via
`ingest/probe.py::probe()`, reused rather than reinvented) is checked against the
requested one; more than ~0.5s over triggers the fallback automatically, no user-facing
toggle.

This sandbox started with no ffmpeg (`test_subvideo_extraction.py`'s `needs_ffmpeg`-
marked tests all skipped). Installing it for real (`apt-get install --no-install-
recommends ffmpeg`) rather than trusting the skip marker caught two problems the
skip would have hidden entirely: two test fixtures assumed a fixture video's duration
would be `None` (true only because ffprobe was unavailable) and broke once it was
real; a `gather()` test's expectation depended on whether the asset happened to have a
poster, which differs by environment. Neither was a product bug, but both would have
looked like passing coverage while testing nothing real. The fallback path itself is
verified with two synthetic fixtures generated on the fly (`ffmpeg -f lavfi testsrc`,
one with keyframes every 10s, one every 1s) so the trigger condition is deterministic
rather than depending on where a keyframe happens to land in the checked-in sample
video.

### `write_file`'s real failure mode is `EXDEV`, not extra I/O

`LocalStorage` gained `write_file(key, source_path)` for adopting an already-on-disk
file (ffmpeg's own output) without reading it fully into memory. The obvious
implementation — `os.rename`/`os.replace(source_path, target)` — works in a dev
environment where `tempfile.TemporaryDirectory()` and the media root happen to share a
filesystem, and fails in a real deployment (a Docker bind-mounted media volume)
with `EXDEV: Invalid cross-device link`, since rename requires same-filesystem. The
implementation is a genuine chunked copy into `<key>.partial` **inside the storage
root**, hashing as it streams (mirroring `write_stream`'s pattern exactly), then
`os.replace`s that `.partial` file into place — always same-filesystem, since both live
under `self.root`.

### Scope cuts

- **Clipping a clip is refused (400), in both directions** — non-destructively and via
  fresh sub-video extraction. The validation pass argued extracting a sub-video *from* a
  clip isn't actually hard (resolve to absolute coordinates against the real ancestor and
  it's an ordinary cut, since a clip is guaranteed one level deep once clip-of-clip is
  refused non-destructively). Weighed and rejected: **promote already does "turn this
  clip into a real file,"** in place, via the same ffmpeg mechanics. A second path to
  the same end state, leaving the clip intact, would be a third coordinate-flattening
  code path for marginal benefit over what promote covers.
- An existing clip's in/out bounds can't be edited after creation — only created or
  promoted.
- AI enrichment (describe/summarize/autotag/generate_all) is unavailable on clips —
  see the `gather()` guard above.

---

## File map

**Backend, new:** `enrichment/subvideo.py` (ffmpeg mechanics), `enrichment/
extract_subvideo.py` (orchestration), `routers/clips.py`, the migration adding
`parent_asset_id`/`in_point`/`out_point` to `asset`.

**Backend, extended:** `models/asset.py` (the three columns), `models/job.py`
(`KIND_EXTRACT_SUBVIDEO`), `ingest/filetypes.py` (`SOURCE_CLIP`/`SOURCE_SUBVIDEO`),
`storage/base.py` + `storage/local.py` (`write_file`), `services/assets.py`
(`create_clip`, `list_children`, `blocking_clips`, `create_subvideo_asset`,
`promote_clip`, `parents_for_many`, `to_read_model`'s parent-resolution), `enrichment/
source.py` (`gather()`'s guard), `jobs/enrichment.py` (dispatch branch, `submit()`'s
`payload` kwarg), `jobs/registry.py` (`result_asset_id`), `schemas_jobs.py`
(`ActivityJobRead.result_asset_id`), `schemas_assets.py` (`AssetRead`'s three new
fields), `routers/assets.py` (the delete guard), `routers/search.py` (parent
resolution for a clip appearing in a search hit), `main.py` (router registration).

**Frontend, new:** `api/clips.ts`, `components/ClipEditor.tsx`.

**Frontend, extended:** `api/assets.ts`, `api/transcripts.ts` (`result_asset_id`,
`activityApi.get`), `components/EnrichmentButton.tsx` (`onFinish` callback — small,
backward-compatible, so "Extract as sub-video" could reuse the whole component rather
than reimplementing its running/error/polling states), `components/AssetDetail.tsx`
(Clip tab, playback bounding, enrichment gating, the delete-guard promote flow).

**Tests:** `test_clips_api.py`, `test_subvideo_extraction.py` (backend); the `clips
(M7)` block in `views/AssetView.test.tsx` (frontend — `AssetDetail` has no dedicated
test file, it's exercised through the view that renders it, matching the existing
convention for that component).

---

## Verification

- `cd backend && pytest -q` — 829 passed, real ffmpeg installed in this sandbox to get
  genuine coverage rather than skips.
- `cd frontend && npx tsc --noEmit && npx eslint . --max-warnings 0 && npx vitest run
  && npm run build` — 264 passed, clean typecheck, clean lint, clean build.
- Manual, in a real dev environment: the plan doc's own M7 acceptance test — cut a clip
  at 01:01–01:10, confirm no new file exists on disk and playback is bounded; extract
  the same range as a sub-video, confirm the file is standalone and the parent is
  untouched; attempt to delete a parent with clips, confirm the guard fires with the
  dependent list, and the promote path clears it and lets the delete through.
