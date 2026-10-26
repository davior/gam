# Import tracks from a page

**Status:** built. Step 3 of making the library a soundtrack source for Gecko Video
Creator, after the music profile and analysis of the sound (`music-profile.md`).

Paste a page from a music library into **Add → "Or paste a page of music tracks"** and
press **Find tracks**. GAM reads the page and lists its tracks with their details;
filter, tick the ones you want (up to 50 at a time) and press **Import**. Each becomes
its own background job: downloaded, catalogued like an upload, given what the page said
about it, then measured for whatever the page left out.

## How a page is read

Best source first. The review list says which one was used.

| Source | When | What it gives |
|---|---|---|
| **incompetech** | `incompetech.com` pages | The page builds its list in the browser from `pieces.json` beside it; GAM reads that file. Title, genre, album, feel (→ moods), instruments, BPM, length, description, upload date, and the licence's exact attribution text as the credit line. Genre and album names come from the lookup tables in the page's own script. |
| **schema.org** | Any page with `MusicRecording` JSON-LD | Title, artist, genre, duration, date, licence, keywords. What sites publish for search engines, so no layout is guessed. |
| **mixkit** | `mixkit.co` (on top of its JSON-LD) | Each card's mood, instrument and tag links, matched to the track by its MP3 URL, plus the mood or instrument the listing itself is filtered to. |
| **AI** | A page with none of the above, when an AI provider is configured and "Ask the AI" is ticked | The model reads the page's text and may only name audio URLs found on the page; anything else it returns is dropped. Costs one completion per page, recorded in Usage. |
| **links** | Otherwise | Every audio file linked or embedded, titled by its link text. |

A page that builds its list with JavaScript from somewhere other than incompetech's
known file has no audio URLs in it for any of these to find; the review says so.

## What lands on the asset

Everything from the page is stamped `embedded` in `field_provenance`, the standing an
imported video's site metadata has: a statement from the source nobody here checked.

| Asset field | From |
|---|---|
| `name` | title |
| `creator`, `publisher`, `source_title` | artist, site, album |
| `license`, `credit_line` | as the page states them; incompetech's credit is its own attribution wording |
| `source_url` | the track's own page when there is one, else the audio file. Also what recognises a re-import: a track already in the library is marked "In library" and not downloaded twice. |
| `retrieved_at` | now |
| `music_genres`, `music_moods`, `music_instruments`, `music_bpm` | the page's |
| tags | the page's tags (Mixkit's "Road Trip", "Lifestyle") |

Tempo, key and energy the page did not give are measured from the sound afterwards, by
the analysis every audio upload gets.

## Downloading

`backend/app/ingest/web.py`: an SSRF check on every redirect hop, uncompressed bodies
only, a 200 MB cap enforced while the bytes arrive, and a response that is not audio
(a login wall, an interstitial) refused rather than catalogued. Requests identify
themselves as GAM. One track per job on the shared enrichment queue, so downloads run one
at a time.

## Licences

Only ticked tracks are fetched, at the pace of one worker: one person importing what they
chose, which is the use these sites' licences describe. Mixkit's licence is its own (not
Creative Commons) and forbids redistributing the files; incompetech's tracks are CC BY 4.0
and need the credit GVC's end credits carry.
