# M6 — AI enrichment: the provider model

**Status:** step 1 of 8 landed (`AIProvider` + migration + CRUD + settings UI, plus both
M5 carry-overs). Steps 2-8 not started. Written after reading `davior/gecko-notes` at
`95ed2ca`.

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
2. `providers/` client with the three protocols, non-streaming, server-initiated.
3. `UsageEvent` + `pricing.py` + a cost readout.
4. `summarize` — simplest job, text in / text out, proves the pipeline.
5. `describe` — needs `supports_images`; the vision path.
6. `autotag` — suggestions, never applied silently.
7. `field_provenance` enforcement — the column is written on manual edits
   (`services/assets.py::apply_metadata`) and **read by nothing**. M6 is where an AI write
   path must consult it before overwriting a human edit (FR 8.1.3).
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
