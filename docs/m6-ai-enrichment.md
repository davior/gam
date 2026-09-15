# M6 — AI enrichment: the provider model

**Status:** steps 1-2 of 8 landed (`AIProvider` + CRUD + settings UI and both M5
carry-overs; then the three protocol clients). Steps 3-8 not started. Written after
reading `davior/gecko-notes` at `95ed2ca`.

This exists because most of what M6 needs already works in gecko-notes, and a session
that starts from `plan-of-attack.md` alone would design it from scratch instead. Read
this before writing any provider code.

---

## First: embeddings and vision are different axes

This is the distinction the whole milestone rests on, and it is easy to get wrong
because both sound like "what the model can handle".

A **generative (chat) model** takes a prompt and returns *text*. Vision — gecko-notes'
`supports_images` — means it can additionally take a picture as **input** and talk about
it.

An **embedding model** takes text and returns **a vector**: coordinates in a space where
similar meanings land near each other. It produces no language at all. Nobody reads its
output; the only operation is measuring distance between two of them. That is the
machinery behind finding "the video where he talks about deploying nano weapons" in a
clip that never says those words.

So they are not two settings on one dial. A provider can be excellent at one and incapable
of the other:

| | Embeddings | Generation + vision |
|---|---|---|
| **Anthropic (Claude)** | **No such API.** Not a gap in GAM — Anthropic does not publish one | Yes, and strong at vision |
| **OpenAI** | Yes (`text-embedding-3-*`) | Yes |
| **DeepSeek** | Unverified — chat API is OpenAI-compatible; confirm before assuming an embeddings endpoint | Yes; chat is **text-only**, see below |
| **Ollama** | Yes (`nomic-embed-text`) | Yes, model-dependent |

**How an image becomes searchable in GAM**: not by embedding the picture. A vision model
looks at it and writes a description; that *text* is embedded. So `supports_images` decides
whether a provider can run the `describe` job — it lives entirely on the generation side and
has nothing to do with the embedding provider.

(Multimodal embedding models that place images and text in one vector space do exist —
CLIP-style — and would let images be searched without captioning first. OpenAI publishes
none. A later option, not Phase 1.)

**Consequence:** GAM keeps **two** provider settings. Not an arbitrary split — two
capabilities with genuinely different provider sets. The embedding one already exists
(`/api/settings/embeddings`, M5). M6 adds the generation one.

---

## What to port from gecko-notes

### `AIProvider` — `backend/app/models.py:146`

Close to exactly right, and already ahead of GAM's embedding settings in three ways GAM
will want:

| Field | Notes |
|---|---|
| `provider_type` | `anthropic \| openai \| deepseek \| ollama \| custom` — DeepSeek already first-class |
| `api_key` | Encrypted at rest via `encrypt_api_key` (`routers/settings.py:217`) — GAM has the same Fernet helpers |
| `base_url` | The configurable endpoint GAM's embedder lacks |
| `model` | **Free text**, not a closed dropdown. GAM's embedding settings has a two-item list and should move to this shape |
| `max_tokens` | Output cap |
| `supports_images` | Gates the `describe` job per provider |
| `use_anthropic_api` | See below — this one is worth understanding properly |
| `extra_params` | JSON-as-text, merged into the request; structural keys stripped first |
| `enabled` / `is_active` / `user_id` | Per-user rows, one active |

### `use_anthropic_api` is not cosmetic

`routers/settings.py:843-880`. DeepSeek publishes an **Anthropic-compatible** endpoint at
`api.deepseek.com/anthropic`, and that endpoint runs the same **server-side `web_search`
tool** Claude does. A DeepSeek provider pointed at it searches the web natively — no
third-party search key, no per-search fee. Its OpenAI-compatible endpoint has no such tool.

Everything downstream keys off `_speaks_anthropic(provider)`, not off the vendor name.
`ollama` is deliberately excluded from `_ANTHROPIC_CAPABLE_TYPES` because its `base_url` is
allowed to be a private address — the one place the app permits that — so honouring the
flag there would aim the Messages proxy at an internal host.

### Fixed managed endpoints are a security decision

`_openai_compat_base` (`:1020`) and `_anthropic_base` ignore any stored `base_url` for
`openai` and `deepseek`. Two reasons, both worth keeping: it spares the user a field, and
it stops a crafted `base_url` redirecting the proxy. `custom` supplies its own, SSRF-checked
on save by `_require_safe_external_url` (`:125`, https-only).

### `UsageEvent` + `pricing.py` — cost visibility

`models.py:188` and `pricing.py` (87 lines). This is FR 8.1.4 and GAM has none of it.

The distinction worth preserving: `cost_estimated=True` marks a **list-price estimate**
from the `pricing.py` table, versus a provider-billed exact amount. `pricing.py`'s own
docstring is explicit that its figures are best-effort and not authoritative — providers
change prices and offer discounts it does not model. Ollama is free; fal bills exactly from
response headers.

**Do not let GAM invent a cost figure without this flag.** M5's settings panel deliberately
shows counts and no currency for exactly this reason, and a test asserts no price renders.

---

## Fix on the way in — do NOT copy verbatim

The plan-of-attack's standing list applies, plus three specific to this area:

1. **The request body is built in the browser.** `assistant/provider.py`'s own docstring
   says so: `ai.ts` assembles it — including four `cache_control` breakpoints whose order
   decides whether the prompt cache hits — and ships it with the job. **GAM cannot reuse
   this.** Enrichment jobs run on a worker thread with no browser anywhere. GAM needs a
   server-side body builder, which gecko-notes does not have.

2. **The proxies are streaming and browser-facing.** `proxy_anthropic` / `proxy_openai` /
   `proxy_ollama` exist for a browser posting to them. GAM's calls are server-initiated and
   non-streaming (a `describe` job wants one answer, not tokens). Port the *endpoint
   resolution, protocol shaping and usage recording*; do not port the streaming plumbing.

3. **`routers/settings.py` is 3,284 lines** carrying providers + speech + images + themes +
   usage + cost. GAM splits this: `app/providers/` for the client, `app/enrichment/` for the
   jobs, `app/usage/` for events and pricing. GAM's own settings router is currently 200-odd
   lines and should stay that shape.

Also: gecko-notes has **no embedding concept whatsoever** (verified by grep). That half is
GAM-only and stays where M5 put it.

---

## What each job reads — and the title nobody specified

Added after the provider work landed, because a question about video enrichment found
that this was never decided.

The milestone above organises jobs by **what they produce** — describe, summarize,
autotag. The axis that actually varies is **what they read**: pixels for an image, a
transcript for video and audio, extracted text for a document. That mismatch is why the
transcript path is only half-specified today. `plan-of-attack.md` says `summarize` is an
"LLM over transcript / extracted document text" and that `describe` is a "Vision LLM",
and says nothing at all about what `autotag` reads.

### Source material, by asset type

One function, consulted by all three jobs, rather than three jobs each deciding for
themselves:

| Asset | Source material | Notes |
|---|---|---|
| Image | the image bytes | Needs `supports_images`; refused before sending otherwise |
| Video or audio **with** a transcript | the transcript text | The case this section exists for |
| Video **without** a transcript | the poster frame | `ingest/thumbnails.py::_from_video` already produces one |
| Document | extracted text | **Nothing produces this yet** — see below |
| Anything else | nothing | The job refuses rather than inventing from a filename |

Two things this table makes visible that were previously implicit:

- **A video's description should come from its transcript, not its poster frame,** when
  it has one. A single frame of a two-hour interview describes a person sitting down. The
  transcript describes what was said, which is what anyone searching is actually looking
  for. The frame is the fallback for silent or untranscribed video, not the primary.
- **`extract_text` does not exist.** `plan-of-attack.md` lists it as a job
  (`pypdf`/`python-docx`/plain read) but there is no `KIND_EXTRACT_TEXT` in
  `models/job.py` and no module for it. So a PDF currently has no text for `summarize` or
  `autotag` to read, and enrichment over documents is blocked on building it. That is a
  gap, not a decision.

### One pass, not three

For a transcribed asset, `describe`, `summarize` and `autotag` should be able to run as a
single call that returns all of its outputs at once, rather than three calls each
re-reading the same transcript.

The reason is cost, and it is not marginal: a two-hour interview transcript is tens of
thousands of input tokens, so three passes is roughly three times the spend for outputs
that would also be more coherent written together. The three job kinds stay as the
user-facing verbs — they are already in `models/job.py` and in
`ActivityIndicator.tsx`'s labels, and a user wants to re-run just the tags sometimes —
but the shared path underneath them should be able to satisfy several at once.

### The title

**Nothing in either document mentions generating one.** `Asset.name` is defaulted from
the filename at ingest and changes only when someone edits it by hand, so a video stays
`IMG_4821.mp4` for as long as it is in the library.

That is worth fixing here rather than later, because `name` is a **search field**:
`asset_fts` indexes `(name, description, summary, tags_text)`, so a library of
filename-named videos is feeding noise into a quarter of the keyword index. M5's own
acceptance query — "the video where James Giordano was talking about deploying nano
weapons" — is exactly the kind of thing a real title helps land.

Two decisions that go with it:

- **Reuse `name`. Do not add a `title` column.** Two fields means answering "which one do
  I show here" at every call site, forever.
- **A generated title is a suggestion, never a direct write** — unlike a description or a
  summary. Those fields are empty until something fills them, so writing one is additive.
  `name` is never empty, so writing it is always a *replacement*, and replacing what a
  user sees in their library without asking is a different act from filling a blank. It
  goes through the same accept/reject path as `autotag` (FR 9.1.4).

### What protects a human edit

`field_provenance` is written in exactly one place — `services/assets.py::apply_metadata`,
which stamps `"human"` — and read nowhere. A freshly ingested asset therefore has `{}`.

That absence is already the signal step 7 needs, so **no new provenance value is
required**: a field with no entry has never been touched by a person and an AI run may
write it; a field marked `"human"` may not be overwritten without asking. An AI write
stamps `"ai"`, which a later AI run is free to replace.

`name` is the exception, and not because of provenance: it is never blank, so the
suggestion rule above applies to it whether or not a person has edited it.

---

## Shape for GAM

```
backend/app/
  models/provider.py     AIProvider, ported
  models/usage.py        UsageEvent, ported
  providers/
    base.py              LLMProvider protocol — complete(messages, images?) -> text + usage
    anthropic.py         Messages protocol; also serves deepseek with use_anthropic_api
    openai.py            OpenAI-compatible; serves openai, deepseek, custom
    ollama.py            its own protocol
    _upstream.py         retry/backoff/timeout translation (port from settings.py)
  usage/
    pricing.py           ported table
    events.py            record_usage(...)
  enrichment/
    describe.py          vision -> description   (requires supports_images)
    summarize.py         transcript/document -> summary
    autotag.py           -> suggested tags, status="suggested" until accepted (FR 9.1.4)
  routers/providers.py   CRUD over AIProvider
```

### Milestone order

1. ~~`AIProvider` + migration + CRUD + settings UI (free-text model, base URL, the
   `supports_images` flag).~~ **Done.** `backend/app/models/provider.py`,
   `app/providers/` (constants + endpoint resolution), `app/routers/providers.py` at
   `/api/providers`, `frontend/src/components/ProviderPanel.tsx`. Four things were fixed
   on the way in rather than ported: the read schema reports `api_key_configured` instead
   of gecko-notes' redact-to-`""` (which cannot distinguish "configured" from "not");
   `api_key: ""` clears a stored key, which gecko-notes has no way to do; `provider_type`
   is validated against an allowlist; and the connection probe SSRF-checks its
   Anthropic-protocol branch, which gecko-notes checks only on the OpenAI-compatible one.
   The guard itself is new — `backend/app/safe_url.py`, GAM's first — and additionally
   resolves the hostname, because checking literal IPs alone lets a name that the caller
   controls point at 127.0.0.1.
2. ~~`providers/` client with the three protocols, non-streaming, server-initiated.~~
   **Done.** `base.py` (the `LLMProvider` protocol, `Completion`, `Usage`, `Image`),
   `anthropic.py`, `openai.py`, `ollama.py`, `params.py`, and `_upstream.py` — the
   retry/backoff this repo had nowhere, so a 429 mid-backfill is now a pause rather than
   a hard failure. `build_provider(session, user_id)` in `providers/__init__.py` mirrors
   `build_embedder`, returning None for an unconfigured library rather than raising.

   Two deviations from the sketch above, both deliberate. `complete()` takes **one
   prompt, not a `messages` list**: gecko-notes needs a message list because it is
   backing a chat in a browser, and GAM's three callers each ask one question and read
   one answer. And `Usage` is **returned, not recorded** — persisting it is step 3, and
   it belongs at the job boundary where the user and asset are in scope, not inside an
   HTTP client.

   The connection probe in `routers/providers.py` was deliberately left alone: it
   answers "is this address reachable and this credential accepted", which is a
   different question from "can this model complete", and folding the two together
   would make both worse.
3. `UsageEvent` + `pricing.py` + a cost readout.
4. `summarize` — simplest job, text in / text out, proves the pipeline. Reads the
   transcript for A/V; see "What each job reads" above for the full table and for the
   shared source-material step all three of these need.
5. `describe` — needs `supports_images`; the vision path. For a **transcribed** video the
   transcript is the primary source and the poster frame is the fallback, not the
   reverse.
6. `autotag` — suggestions, never applied silently. Same source material as the two
   above; a generated **title** rides this same suggestion path.
7. `field_provenance` enforcement — the column is written on manual edits
   (`services/assets.py::apply_metadata`) and **read by nothing**. M6 is where an AI write
   path must consult it before overwriting a human edit (FR 8.1.3). No new provenance
   value is needed; an absent entry already means "no person has touched this".
8. Bulk enrichment over a selection — including the `SelectionBar` embed deferred from M5.

### While here, two M5 carry-overs

Both done alongside step 1.

- ~~Give the **embedding** provider a `base_url` and a free-text model, matching this
  shape.~~ `EMBEDDING_BASE_URL` in `settings_store`, `OpenAIEmbedder(base_url=…)`, and an
  "OpenAI-compatible endpoint" field on the panel, SSRF-checked on save. That is what
  would let DeepSeek serve the embedding side too, if it exposes an endpoint.
- ~~The embedding settings' two-item model dropdown becomes suggestions, not a closed
  list.~~ An `<input list>` over a `<datalist>`, committed on blur rather than per
  keystroke. The server already accepted any string; only the UI was closed.

---

## Verification

Per milestone: `cd backend && pytest -q`, `cd frontend && npm test && npm run build`.
ffmpeg must be on `PATH` or six unrelated tests fail and look like a regression.

The one that matters: configure a **DeepSeek** provider and a **Claude** provider, run
`describe` on an image with each, and confirm the text-only one is refused by the
`supports_images` check rather than failing with a deserialization error from upstream —
which is the failure mode the flag exists to prevent.
