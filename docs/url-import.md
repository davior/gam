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
| clips | `chapters` | Named "Chapter — Video", clamped to the file's real length; a lone chapter spanning the whole video is skipped. Every chapter shows the site thumbnail — the same file as the video's — so deleting one chapter leaves the video's file and its thumbnail alone. |
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
| `docker-compose.cookies.yml` | Opt-in overlay that mounts `secrets/` read-only and points the importer at the cookies file. See [YouTube cookies](#youtube-cookies). |

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

### YouTube cookies

An import that fails with *"The site wants this server to sign in first"* has hit
YouTube's "Sign in to confirm you're not a bot" wall, which it shows to many VPS and
datacenter IP ranges. Age-restricted and members-only videos need a signed-in session
too. The fix is to give the importer one: a cookies file exported from a browser.

Before starting:

- **The file is a login.** Anyone holding it is signed in as that account. Keep it out of
  chats and out of git — `/secrets/`, where it goes, is gitignored for this reason.
- **Use a spare Google account.** An account used for automated downloading can get
  flagged by YouTube, and a spare one keeps your main account out of that.

The steps use Firefox, because its cookie store is not encrypted and yt-dlp reads it
directly. Everything here assumes Linux.

#### On your own computer

1. **Install yt-dlp.** It is only used here for the export, so any recent version works:

   ```bash
   pipx install yt-dlp        # or your distro's yt-dlp package
   ```

2. **Make a Firefox profile just for this.** Open `about:profiles`, choose *Create a New
   Profile* (call it `gam-youtube`), then *Launch profile in new browser*. In that window,
   sign in to YouTube with the spare account and play any video. Then close the YouTube
   tab and **quit Firefox completely**.

   A separate profile, because YouTube rotates the cookies of a session left open in a
   tab, which invalidates an exported copy within hours. A profile you never open YouTube
   in again keeps them valid, and its export holds nothing else of yours. (yt-dlp's own
   advice is a private window, but a private window's cookies are never written to disk,
   so `--cookies-from-browser` cannot see them.)

3. **Find the profile's folder.** In `about:profiles`, copy the new profile's **Root
   Directory**. Its folder name is a random prefix plus the profile name, such as
   `ab12cd34.gam-youtube`, under one of these depending on how Firefox is installed:

   | Firefox install | Profiles live under |
   |---|---|
   | Snap (Ubuntu's default) | `~/snap/firefox/common/.mozilla/firefox/` |
   | Flatpak | `~/.var/app/org.mozilla.firefox/.mozilla/firefox/` or `~/.var/app/org.mozilla.firefox/config/mozilla/firefox/` |
   | Profiles created before Firefox 147 | `~/.mozilla/firefox/` |
   | New installs of Firefox 147 or later | `~/.config/mozilla/firefox/` |

4. **Export**, naming that folder explicitly (this example is the Snap location):

   ```bash
   yt-dlp --cookies-from-browser "firefox:$HOME/snap/firefox/common/.mozilla/firefox/ab12cd34.gam-youtube" \
          --cookies cookies.txt
   ```

   It ends with `error: You must provide at least one URL.` That is expected and
   harmless — the file was written before it. **Always give the path.** A bare
   `--cookies-from-browser firefox` reads whichever profile was used most recently, which
   is usually your everyday one: every site you are signed in to, and the wrong YouTube
   session.

5. **Keep only the YouTube lines**, then delete the full export:

   ```bash
   grep -E $'^(#|\\.?youtube\\.com\t)' cookies.txt > youtube-cookies.txt
   rm cookies.txt
   ```

   The export holds every cookie in the profile, Google's own sign-in cookies included.
   The importer needs only `youtube.com`'s.

#### On the server

1. **Put the file in `secrets/`**, beside `docker-compose.yml`:

   ```bash
   # on the server, in the gam checkout
   mkdir -p secrets && chmod 700 secrets

   # from your computer
   scp youtube-cookies.txt you@your-server:/path/to/gam/secrets/

   # on the server again
   chmod 600 secrets/youtube-cookies.txt
   ```

2. **Add the cookies overlay to `COMPOSE_FILE`** in `.env`:

   ```env
   COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml:docker-compose.cookies.yml
   ```

   (Without the reverse-proxy overlay, `docker-compose.yml:docker-compose.cookies.yml`.)
   `docker-compose.cookies.yml` mounts `secrets/` read-only at `/app/secrets` and points
   `URL_IMPORT_COOKIES_FILE` at `/app/secrets/youtube-cookies.txt`. Set that variable in
   `.env` only to use a different file name.

3. **Apply it**, and check the container can see the file:

   ```bash
   docker compose up -d
   docker compose exec backend ls -l /app/secrets/
   ```

   No rebuild is needed. Then retry the import that failed.

**Not `docker-compose.override.yml`.** An earlier version of these docs said to put the
mount there. Compose reads that file only when `COMPOSE_FILE` is unset, and this
deployment sets `COMPOSE_FILE` for the reverse proxy — so the mount would be silently
ignored. The overlay is named in `COMPOSE_FILE` explicitly for that reason.

Each import hands yt-dlp a *copy* of the file, because yt-dlp writes the cookie jar back
out when it finishes. The read-only mount is therefore fine, and your file is never
rewritten.

#### When it stops working

The sign-in message coming back means YouTube has expired or revoked that session.
Launch the `gam-youtube` profile, sign in again if asked, play a video, close the tab and
quit Firefox. Then repeat steps 4 and 5 on your computer and copy the new file over the
old one on the server. No restart: the directory is mounted, not the file, so the next
import reads the new one.

If every import instead fails with *"URL_IMPORT_COOKIES_FILE is set to …, but there is no
file there"*, the overlay is on but `secrets/youtube-cookies.txt` is missing or named
differently.

#### Other browsers

`--cookies-from-browser` also accepts `chrome`, `chromium`, `brave` and `edge`. On Linux
those encrypt their cookie store with the desktop keyring, and yt-dlp may need the keyring
named — `chrome+gnomekeyring`, for instance; `yt-dlp --help` lists the choices. Firefox
needs none of that.

### Disk

A download goes to a temp directory in the container, then is copied into the media tree
— the same shape sub-video extraction uses. A long 1080p video needs roughly twice its
size free while that happens.

### Settings

| Variable | Default | |
|---|---|---|
| `URL_IMPORT_MAX_HEIGHT` | `1080` | Tallest video fetched. |
| `URL_IMPORT_MAX_PLAYLIST_ITEMS` | `200` | Most videos one playlist or channel link may queue. |
| `URL_IMPORT_COOKIES_FILE` | empty | Set for you by `docker-compose.cookies.yml` — see [YouTube cookies](#youtube-cookies). |

---

## Known limitations

Recorded so a later session can tell a gap from a decision.

- **A video with chapter clips asks before it goes.** M7's delete guard still stops a
  plain delete of a parent with live clips, so twenty curated chapters are never lost by
  accident; it now offers **Delete clips too** beside **Promote and delete**, which
  removes the video and every chapter in one step and one transaction. A single chapter
  can be deleted from the video's Clip tab.
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
