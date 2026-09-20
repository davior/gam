# M10 — Attribution

**Status:** built. The spec below held with three changes, each recorded in place:
a library-wide re-harvest was added (the files already uploaded carry their metadata on
disk and nothing had ever looked), a bulk attribution pass over a selection was built
rather than deferred, and `rights_notes` stayed out as decided.

Numbered M10 but landing **before** M8 and M9. Out of numeric order on purpose: M8 needs
a fal.ai account and M9 needs GVC to exist, while this needs neither and is the one piece
of work that gets more expensive every day it is deferred.

---

## Why this exists

GAM records *how* a file entered the library and nothing about **whose work it is**.
`Asset.source` (`local_upload | url | ai_generated | gvc_export | clip | sub_video`) is
routinely mistaken for this and is not: it is a pipeline fact, not a credit. There is no
field for a source URL, an author, a publisher, or the programme a clip was taken from,
no UI for one, and nothing in M8–M9 that adds one.

The cost of deferring is asymmetric, which is why this jumped the queue. Attribution
captured at ingest costs a form field. Attribution reconstructed later means opening every
file by hand and guessing — and for material gathered from the open web, the guess is
often unrecoverable, because the tab it came from is closed and the page may be gone. A
library that cannot say where anything came from also cannot be published from: GVC cannot
build a credits roll out of prose, and an uncreditable asset is one that has to be left out.

The user asked for this before adding more content. That is the correct order.

---

## The three decisions

Each was put to the user explicitly. The rejected alternatives are recorded because the
reasons are not recoverable from the schema alone.

### 1. Structured fields, not a free-text credit

**Chosen:** eight columns. **Rejected:** a single `credit_line` string.

A free-text credit is faster to fill in and impossible to get structurally wrong, and it
fails the moment the library is asked a question. "Everything from this programme",
"everything published before 2020", "everything still missing a source" are all
unanswerable against prose without parsing it, and a parser over hand-written citations is
a worse problem than the one it solves. Downstream, GVC building a credits roll needs the
publisher as a field, not as a substring.

A middle option — four fields (`source_url`, `creator`, `publisher`, `credit_line`) — was
offered and declined. It is less to type per asset and it defers exactly the columns that
cannot be added cheaply later: adding `published_date` to a populated library means a
migration *and* a manual re-entry pass over every row, because the information was never
captured.

A ninth field, free-text `rights_notes`, was offered alongside the eight and **not**
taken. Recorded here so a later session reads it as a decision rather than an oversight.
The messy cases it was meant to hold — "cleared for non-commercial use", "permission by
email 2024-03-11", "unknown, found on a forum" — currently go in `license`, which is free
text. If that field starts accumulating sentences rather than licence names, that is the
signal to revisit this, and the migration is additive.

### 2. Embedded metadata is written directly; AI may only suggest

**Chosen:** two separate paths, deliberately asymmetric.

**Embedded metadata is a fact about the file.** A JPEG's EXIF `Artist`, an MP3's ID3 tags,
an MP4's `format.tags`, a PDF's `Author` — these were written by whoever produced the
file. Reading them is not inference, so they are written straight into empty fields at
ingest. GAM is currently discarding this: `ingest/probe.py` already invokes ffprobe with
`-show_format`, which returns `format.tags`, and the result is parsed for duration and
codec and thrown away. The data is already on the wire.

**What a model reads off a chyron is a claim.** It goes through the existing `Suggestion`
accept/reject flow and never touches the asset until a person says yes. The reasoning is
specific and worth stating plainly: a fabricated citation is worse than a blank one. A
blank field is visibly incomplete and prompts the user to fill it. A confidently wrong
publisher looks finished, gets copied into a credits roll, and ends up attributing
somebody's work to the wrong outlet. The failure modes are not symmetric, so the trust
model is not either.

This is also why attribution does **not** follow the `describe`/`summarize` pattern, where
pressing Generate overwrites the field. That pattern is right for a description — an
inaccurate description is a poor search result. It is wrong for a citation.

**The grounding rule.** The proposal prompt requires the model to return a field only
where it can quote the text it read the value from: a visible chyron, a byline, a title
page line, a watermark. That quote is stored on the suggestion as `evidence` and shown in
the accept UI, so accepting is an informed act rather than a reflex. A field the model
cannot ground is omitted rather than guessed, and the test suite asserts that an
ungrounded reply produces no suggestions at all.

### 3. Clips inherit from the parent, with per-field override

**Chosen:** resolve through `parent_asset_id` at read time.
**Rejected:** copy the parent's values when the clip is cut.

Copying is simpler to query and makes each clip self-contained, and it rots. Discovering
six months later that a documentary's publisher was recorded wrong means every clip cut
from it before the correction keeps the wrong value forever, silently, with nothing to
indicate they disagree with their own parent. Citation records that drift apart from the
source they cite are worse than no records, because they are trusted.

Resolution at read time costs nothing here: `to_read_model` is already passed the parent,
and `parents_for_many` already batch-loads parents for both listings and search results,
because M7 needed exactly that to resolve a clip's `file_url`. The pattern is in place.

Override is per field, not all-or-nothing, so a clip can carry its own timestamped
citation or a different creator for one interviewee without discarding the parent's
publisher and source title.

Inheritance is **one level only**, and that is sufficient rather than a simplification:
`create_clip` refuses to clip a clip, and `promote_clip` leaves `parent_asset_id` pointing
at the original. There is no chain to walk, and the code should say so, so nobody adds a
recursive resolver for a depth that cannot occur.

---

## Schema

Eight columns on `Asset`, in their own block after the transcript header.

| Column | Type | Notes |
|---|---|---|
| `source_url` | `str \| None` | Where it came from. Not validated as reachable — a dead link is still a record, and often the only one. |
| `creator` | `str \| None` | Author, photographer, speaker, director. |
| `publisher` | `str \| None` | Outlet, channel, studio, imprint. |
| `source_title` | `str \| None` | The programme, film, article or book this is part of. |
| `published_date` | `str \| None` | ISO 8601 partial date — see below. |
| `retrieved_at` | `datetime \| None` | When it was downloaded or captured. Naive UTC from `app.clock.utcnow`. |
| `license` | `str \| None` | Rights status, free text. |
| `credit_line` | `str \| None` | Override only — see below. |

### Why `published_date` is a string

This breaks the house rule that timestamps are naive UTC `datetime`s, and the model
comment must say why so it does not get "fixed" later.

Publication dates are routinely partial. A book is from 1994. A magazine piece is from
March 2019. A broadcast has a full date. A `datetime` column cannot represent the first
two at all — it forces a fabricated precision (1994-01-01) that is then indistinguishable
from a real one, which in a citation record is exactly the kind of quiet falsehood this
milestone exists to prevent.

The column stores `YYYY`, `YYYY-MM` or `YYYY-MM-DD`, regex-validated on write. ISO partial
dates sort and range-compare correctly as plain strings — `"2018-12-31" < "2019" <
"2019-03-01"` — so the date filters are lexicographic comparisons with no parsing and no
special cases.

`retrieved_at` is a real `datetime`, because a download happened at an instant and there
is no partial-precision case to serve.

### Why `credit_line` is an override, not the composed value

`credit_line` is `NULL` until somebody types one. The displayed credit is composed on read
from the other fields.

Storing the composed string would make it stale the instant any component field is
corrected — the same rot that made copy-on-create the wrong answer for clips, one level
down. The API therefore exposes both: `credit_line` (the raw override, for the edit form)
and `credit` (the resolved display string, composed unless overridden).

### Why a third provenance value

`field_provenance` gains `"embedded"` beside `"human"` and `"ai"`.

An embedded tag is a fact about the file but not a claim a person has verified — an EXIF
`Artist` is often the camera owner's name, or a studio's default, or blank-but-not-empty.
Keeping it distinct from `"human"` is what allows a later AI suggestion or a bulk pass to
propose over an embedded value without ever proposing over something the user typed. Fold
it into `"human"` and that distinction is gone permanently, because nothing else records it.

---

## Migrations — two revisions, deliberately

`f68af8d` exists because `7d4b9c1a6f28` failed against a populated database. The second
half of this change carries the same risk, so it is split rather than stranding the
columns if the risky half fails:

1. **`add_attribution_columns`** — eight `add_column` calls on `asset` inside
   `op.batch_alter_table`, following the pattern `7d4b9c1a6f28` had to learn.

2. **`rebuild_asset_fts_with_attribution`** — `asset_fts` gains an indexed
   `attribution_text` column.

   **SQLite FTS5 does not support `ALTER TABLE ... ADD COLUMN`.** The virtual table must
   be dropped and recreated, and because `asset_fts` stores its own copy of the text
   rather than using external-content mode (see the reasoning at the top of
   `search/fts.py`), dropping it **loses the index for every existing asset**. The
   migration must therefore repopulate, in raw SQL, from `asset` left-joined through
   `assettag`/`tag` for the tag text. A recreate that skips the repopulate passes every
   test that only checks the schema, and silently empties keyword search in production.

   The migration carries its own literal copy of the new DDL, per the convention
   `search/fts.py` already documents. `test_migrations.py`'s drift check
   (`test_migrations.py:80`) extends to the new column set, and a new test asserts the
   rebuild preserves previously indexed rows.

---

## Backend shape

### `app/attribution.py` (new)

- `ATTRIBUTION_FIELDS` — the field-name tuple. The harvester, the suggestion path, the
  FTS text builder and the inheritance resolver all read it, so they cannot drift apart.
- `compose_credit(...) -> str` — pure, session-free, directly unit-testable. Order is
  creator — *source_title* — publisher — published_date — license, skipping blanks and
  emitting no orphaned punctuation for a single-field asset or an empty string for a blank
  one.
- `resolve(asset, parent) -> ResolvedAttribution` — per-field coalesce, clip's own value
  wins. One level, with the comment explaining why that is complete.

### `app/ingest/embedded_metadata.py` (new)

Best-effort throughout and never raises. It runs inside `_describe`, which is already
post-commit precisely so that a failure here cannot lose an upload.

- **video / audio** — `ProbeResult` gains `tags: Mapping[str, str]`; ffprobe already
  returns them. `artist`/`album_artist` → creator, `publisher`/`copyright` →
  publisher/license, `date`/`creation_time` → published_date, `title` → source_title,
  `comment`/`purl` → source_url.
- **images** — Pillow EXIF `Artist` (0x013B), `Copyright` (0x8298), `DateTimeOriginal`
  (0x9003), plus IPTC `By-line`/`Credit`/`Source` via `IptcImagePlugin`. No new dependency.
- **PDF** — pypdfium2 document metadata: `Author`, `Title`, `CreationDate`.
- **docx / pptx / xlsx** — `core_properties.author` / `.title` / `.created`. All three
  libraries are already dependencies, added for `extract_text`.

Writes only into empty fields, stamped `"embedded"`.

### `services/assets.py`

- `apply_metadata` and `apply_ai_metadata` share a private
  `_apply(session, asset, changes, provenance)`; a third wrapper
  `apply_embedded_metadata` joins them. Both existing functions keep their signatures and
  behaviour exactly.
- `_reindex` composes `attribution_text` from the asset itself — no extra query, and for
  the same reason it already reads tags rather than accepting them from callers: one
  function every write path calls is one that nobody can forget.
- `to_read_model` resolves inheritance from the `parent` it already receives.

### API

- `AssetRead` gains the eight fields, plus read-only `credit` (resolved) and
  `attribution_inherited: list[str]` naming which fields came from the parent, so the UI
  can mark them without a second request.
- `AssetUpdate` gains the eight as editable, with the `published_date` regex validator.
- `list_assets` gains `creator`, `publisher`, `source_title`, `published_after`,
  `published_before`, and **`unattributed: bool`** — the last is how a backlog actually
  gets worked through, and is the most operationally useful filter in this milestone.
- `POST /api/assets/harvest-attribution` — a library-wide job re-reading embedded metadata
  for assets already uploaded. `997f000122a7` made `EnrichmentJob.asset_id` nullable for
  exactly this shape; it follows `routers/embeddings.py`'s backfill endpoint.

### Suggestions

`KIND_ATTRIBUTION = "attribution"`, one row per proposed field, `value` holding
JSON-as-TEXT `{"field": ..., "value": ..., "evidence": ...}`. One kind rather than eight,
and still per-field accept/reject. `enrichment/attribute.py` reuses `enrichment/source.py`
to choose the source material rather than rebuilding that decision. Accept dispatches to
`apply_metadata` — the *human* path, stamping `"human"` — exactly as `KIND_TITLE` does.

---

## Frontend

- `components/AttributionPanel.tsx` (new) — a tab in `DetailDock`'s `Tabs` beside
  transcript / document / clips. Eight inputs, a live preview of the composed credit, and
  a **Copy credit** button, which is the reason the composed field exists at all.
  Inherited values render greyed with an "inherited from *parent*" note and an override
  control.
- `components/SuggestionPanel.tsx` — renders the new kind as field, proposed value and the
  quoted evidence.
- `components/FilterBar.tsx` — creator and publisher inputs, plus an **Unattributed** chip.
- `components/AssetDetail.tsx` — the credit line in the facts block.

The panel's inputs must call `stopPropagation` on Escape. The existing bug — `TagInput`
and the transcript editor let Escape reach `DetailDock`'s window listener and close the
whole panel — is **not** in this milestone's scope, but this must not become a ninth
instance of it.

---

## What this deliberately does not do

- **No fetching metadata from `source_url`.** OpenGraph/oEmbed lookup against a
  user-supplied URL is an outbound request to an arbitrary host; it needs `safe_url.py`,
  a job, and a rate-limit story. Worth doing, and it belongs with URL import, which is its
  own outstanding item.
- **No rights-clearance workflow.** `license` records what is known. Whether something may
  be used is a judgement, not a column.
- **No credits-roll export.** That is GVC's job, and this milestone exists to give it
  something to read.
- **No per-clip timestamped citation format.** A clip can override `credit_line` freehand;
  a structured "at 04:12" convention can wait until GVC shows what it needs.

## ~~Open, and genuinely undecided~~ Resolved during the build

Whether a bulk attribution pass over a selection was worth building. **Built.** It turned
out to be one entry in `enrichment/bulk.py::ACTIONS` and one button in `SelectionBar`,
because the machinery M6 put in place already covered it — well under the cost of the
design discussion the question implied. It writes nothing either way, since every result
is a suggestion, so it carries none of the risk that keeps transcription out of that list.

## What the build added beyond the spec

- **A library-wide re-harvest** (`POST /api/assets/harvest-attribution`). Ingest handles
  new uploads, but everything uploaded before M10 still has its EXIF and ID3 on disk with
  nothing having looked. Safe to run repeatedly, because the harvest only fills blanks.
- **Child re-indexing on an attribution change.** Inheritance resolves on read everywhere
  except the keyword index, which by nature stores a snapshot. Without this, correcting a
  parent's publisher left every clip of it indexed under the old one — findable by a value
  no longer shown anywhere.

## Two bugs real files caught that a stubbed tag dictionary would not have

Both would have passed against mocked metadata, and both are why the fixtures are real
files built by `tests/make_fixtures.py`:

- **EXIF `DateTimeOriginal` lives in the Exif sub-IFD (0x8769), not IFD0.** A harvester
  reading only the top level works on hand-built fixtures and fails on every actual
  photograph. The fixture generator carries the matching trap: Pillow serialises the
  sub-IFD from the value stored under 0x8769, so mutating the dict `get_ifd()` returns is
  silently dropped on save — which produces a fixture with no date at all for a broken
  reader to "pass" against.
- **PDF writes its date as `D:20190315101112Z`** — prefixed and separator-less, unlike
  every other format, and unmatched by a normaliser written against the rest.

---

## Verification

Backend (`cd backend && pytest -q`):
- `compose_credit` over each field subset, including a single-field asset (no orphaned
  punctuation) and an all-blank one (empty string, not a bare separator).
- Inheritance: a clip with no attribution resolves the parent's; a clip overriding
  `publisher` keeps the parent's `creator`; correcting the parent changes the clip's
  resolved value on next read.
- Harvest per format against the existing `backend/tests/fixtures/` media, asserting the
  `"embedded"` stamp and that a non-empty field is never overwritten.
- Migrations: both run against a **populated** database; the FTS rebuild preserves every
  previously indexed asset; the drift check covers the new column.
- Search and filters: findable by publisher and by `source_title`; `unattributed=true`;
  `published_after=2019` excludes `2018-12-31` and includes `2019-03-01`.
- Suggestions: a grounded reply creates one row per field; an ungrounded one creates none;
  accept writes through `apply_metadata` and stamps `"human"`; reject is remembered.

Frontend (`cd frontend && npm test && npm run build`) — panel renders, edits and saves;
inherited fields show their origin; the composed preview updates live; Copy credit writes
the resolved string.

End to end (`uvicorn --port 8001` + `npm run dev` on 5174, after `alembic upgrade head` —
migrations do not run outside Docker and this adds two):

1. Upload a JPEG carrying EXIF `Artist` and an MP3 with ID3 tags; both land attributed and
   marked as having come from the file.
2. Upload a file with no embedded metadata, fill it in by hand, confirm the credit composes
   and the asset is findable by publisher in `/search`.
3. Cut a clip of an attributed video; confirm the inherited credit, then correct the
   parent's publisher and confirm the clip follows.
4. Run an attribution suggestion over a document with a visible byline; confirm the
   evidence quote is shown and that accepting writes the field.
5. `unattributed=true` returns exactly the assets still missing a source.
