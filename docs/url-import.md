# Import from URL

**Status:** built. Closes the M1 gap `plan-of-attack.md` recorded as "URL import was
specified and never built", and the M10 deferral "no fetching metadata from
`source_url` — it belongs with URL import".

Paste a link under the drop zone. A background job downloads it with
[yt-dlp](https://github.com/yt-dlp/yt-dlp), catalogues it through the same pipeline an
upload takes, and fills in what the site said about it: title, description, channel,
date, licence, tags, chapters, and — when there is no Deepgram key — its captions as the
transcript. It shows in the activity indicator like any other job, and lands at the top
of the library grid when it finishes.

---

## Decisions

Each was put to the user. The reasons are recorded because the code alone does not
preserve them.

| Question | Decision | Why |
|---|---|---|
| Which sites | **Any site yt-dlp names** (~1,800: YouTube, Rumble, Odysee, BitChute, Vimeo, X, podcasts…) — **except its `generic` extractor** | The code is identical either way, and a video mirrored elsewhere after a takedown should still have a way in. `generic` is the fallback that scrapes *any* page for something playable; it is the part a pasted URL could aim at an internal address, and the part most likely to import the wrong thing. |
| Captions | **Fallback only.** Deepgram when a key is configured; the site's captions when not | Deepgram is diarised, word-timed and punctuated, and the upload chain already queues it. Without a key, a video that stores its captions is searchable by what was said the moment it lands, rather than never. |
| Uploader tags | **Applied by default**, with a per-import checkbox that routes them to suggestions instead | The user's call. It differs from autotag's rule that nothing is ever applied silently (FR 9.1.4), and deliberately: that rule exists because a model's tags are a guess. An uploader's tags are what they chose to call their own video — closer to embedded metadata than to inference. The checkbox is for channels that pad their tags for search. |
| Chapters | **One clip per chapter**, per-import checkbox | Chapters are someone else's already-done work of finding the moments. Clips cost no disk. |
| Playlists | **One job per video** | Each download is long, independently fallible and independently cancellable; a failure on video 30 should cost video 30, not 31–50. |
| Audio only | Per-import checkbox, `.m4a` | Talks and podcasts where the picture is a static frame, at a tenth of the size. |
| Quality | **1080p cap**, H.264/AAC preferred, `URL_IMPORT_MAX_HEIGHT` to change | 1080p is the highest YouTube serves as H.264, the codec every browser (Safari included) plays from MP4 without a re-encode. Above it is VP9/AV1. |

## What the site says, and where it goes

| GAM field | From | Notes |
|---|---|---|
| `name` | `title` | Also the display filename's stem. |
| `description` | `description` | Capped at 20,000 characters, the same limit a hand edit has. The Describe button replaces it on request. |
| `source_url` | `webpage_url` | The canonical address, which is also what de-duplicates. |
| `publisher` | `channel`, else `uploader` | The channel is the outlet. |
| `creator` | `creators` / `artists` only | **Never copied from the channel.** On a news or interview channel the channel is rarely the person speaking, and "Creator: BBC News" is the confidently wrong citation M10 exists to prevent. The attribute job can propose the speaker from the transcript, with evidence. |
| `source_title` | `series`, else `album`, else `title` | `name` is the user's to rename; the citation keeps what the work was published as. |
| `published_date` | `release_date`, else `upload_date`, else `release_year` | Through `normalise_partial_date`, so a bare year stays a year. |
| `license` | `license` | Only when stated. YouTube reports Creative Commons and says nothing for its standard licence; nothing is invented. |
| `retrieved_at` | now | The one attribution field only an import can know — M10 left it with no automatic writer until this. |
| thumbnail | the site's | The picture people remember the video by, and the only one an audio import gets. |
| tags | `tags` | Whitespace collapsed, case-insensitive duplicates and anything over 80 characters dropped, 50 at most. |
| clips | `chapters` | Named "Chapter — Video", clamped to the file's real length; a lone chapter spanning the whole video is skipped. |
| transcript | captions, VTT | Only without a Deepgram key. Hand-written beats automatic; the video's own language beats YouTube's auto-translations. Recorded as model `youtube-captions` or `youtube-auto-captions`, so it is visible which kind a search is matching. |

Everything from the site is stamped `embedded` in `field_provenance`: a statement of fact
from the source that no person here has checked — the same standing as an EXIF Artist.

## How it fits together

| File | Role |
|---|---|
| `backend/app/routers/imports.py` | `POST /api/assets/import`. Normalises the URL, checks it against `safe_url`, queues the job. Makes no yt-dlp call — reading a page takes seconds. |
| `backend/app/ingest/ytdlp.py` | The only module that imports `yt_dlp`: the options, the probe, the download, the error rewording, and the pure info-dict → fields mapping. |
| `backend/app/ingest/captions.py` | VTT → transcript segments, including undoing YouTube's rolling duplicate lines. Pure. |
| `backend/app/enrichment/import_url.py` | The job body: playlist fan-out, dedup, download, `ingest_file`, then metadata, thumbnail, tags, clips, captions. |
| `backend/app/services/assets.py::ingest_file` | The sync twin of `ingest_upload`. Both end in the same `_catalogue`, so an import is probed, thumbnailed, indexed and queued for Deepgram by the upload's own code. |
| `backend/app/enrichment/transcribe.py::store_transcript` | The transcript writer, split out of the Deepgram job so captions and Deepgram store identically. |
| `frontend/src/components/UrlImport.tsx` | The form under the drop zone. |
| `frontend/src/views/LibraryView.tsx` | Adds a finished import (and its chapter clips) to the grid, on seeing its job go from running to done. |

`KIND_IMPORT_URL` is a library kind: the asset does not exist when the job is queued. The
request rides in `EnrichmentJob.payload`, and the finished job adds `created_asset_id`
there — the same field an M7 "extract" job uses, so `result_asset_id` on the activity row
works for both. No migration was needed.

Security: the URL is checked twice — by the router, and again by the job, because a
playlist's entries are URLs the *site* chose and the router never saw them. `generic` is
excluded from yt-dlp's extractors. Downloads land under fixed filenames (`media.%(ext)s`)
rather than names built from the title, which is text the site controls.

---

## Operating it

### When imports start failing

YouTube changes under yt-dlp every few weeks, and a pinned yt-dlp eventually stops
working on its own schedule. The error message says so ("…yt-dlp needs updating"). The
fix is always the same:

1. Set `yt-dlp[default,deno]==<latest>` in `backend/requirements.txt`
   ([releases](https://pypi.org/project/yt-dlp/#history)).
2. `docker compose up --build -d`.

### Deno

Since late 2025 YouTube serves almost no downloadable formats to a client that cannot run
its JavaScript challenges. yt-dlp solves them with an external runtime: the `deno` extra
installs Deno from PyPI (manylinux wheels, x86_64 and aarch64, about 40 MB) and the
`default` extra brings the `yt-dlp-ejs` solver scripts. The backend logs a warning at
startup if `deno` is not on `PATH`.

### "Sign in to confirm you're not a bot"

YouTube shows this to many datacenter and VPS IP ranges; home connections usually never
see it. Age-restricted videos need a signed-in session too. Export your browser's
`youtube.com` cookies in Netscape format (the
[yt-dlp FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)
covers how, and why a private window is best), mount the file read-only into the backend,
and point `URL_IMPORT_COOKIES_FILE` at it. yt-dlp is given a *copy*, because it writes the
jar back out when it finishes. Treat the file like a password: it is a signed-in session.

### Disk

A download goes to a temp directory in the container, then is copied into the media tree
— the same shape sub-video extraction uses. A long 1080p video needs roughly twice its
size free while that happens.

### Settings

| Variable | Default | |
|---|---|---|
| `URL_IMPORT_MAX_HEIGHT` | `1080` | Tallest video fetched. |
| `URL_IMPORT_MAX_PLAYLIST_ITEMS` | `200` | Most videos one playlist or channel link may queue. |
| `URL_IMPORT_COOKIES_FILE` | empty | See above. |

---

## Known limitations

Recorded so a later session can tell a gap from a decision.

- **A video with chapter clips cannot be deleted directly.** M7's delete guard blocks a
  parent with live clips, so a twenty-chapter import needs its clips removed or promoted
  first. Untick "Chapters as clips" for anything you may not keep. A "delete with its
  clips" option is the natural follow-up.
- **Downloads share the enrichment worker.** With the default `enrichment_concurrency`
  of 1, a long download holds up transcriptions queued behind it, and a playlist queues
  its videos one after another.
- **A long playlist fills the grid on the next load, not live.** The activity feed shows
  the ten most recent jobs, and the grid adds an import when it sees that import's job
  finish. In a forty-video playlist most of the jobs are never in those ten, so their
  videos appear when the library next loads. The activity indicator still counts them.
- **A restart mid-download starts that download over.** The job is requeued like any
  other; the partial file was in a temp directory that did not survive.
- **Real sites are not exercised in CI.** `tests/test_url_import.py` replaces yt-dlp with a
  fake that writes real fixture media, so everything downstream of the download runs for
  real, and the options handed to yt-dlp are asserted on. Whether YouTube accepts them
  on a given day is only answered by importing something.
- **Terms of service.** Most sites' terms forbid downloading. What to import is the
  user's call; the importer records full attribution, which is what makes crediting
  possible later.
