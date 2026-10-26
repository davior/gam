# Music profile

**Status:** built. Step 1 of making the library useful as a soundtrack source for Gecko
Video Creator. GVC searches GAM for music, and until this an audio asset carried nothing
that said what it sounds like, so a search could only match a track's title.

## Fields

On `Asset`, all nullable. Parsing and the field list live in `backend/app/music.py`.

| Field | Type | Notes |
|---|---|---|
| `music_genres` | JSON list | "Ambient", "Cinematic" |
| `music_moods` | JSON list | "Tense", "Uplifting" |
| `music_instruments` | JSON list | "Piano", "Strings" |
| `music_bpm` | float | 20–300. Outside that it is a track number or a year in the wrong frame |
| `music_key` | string | As written: "Am", "A minor" and Camelot "8A" are kept as they are, not converted |
| `music_vocals` | bool | **NULL is "not known"**, not instrumental |
| `music_energy` | `low` / `medium` / `high` | Three levels because people can't reliably tell a 6 from a 7 |

## Where values come from

**The file's own tags,** on upload and in the existing "re-read file metadata" job
(`harvest_attribution`), audio only. Read from the ffprobe tag block `probe.py` already
collects, with nothing new to install:

| Field | ID3 (MP3) | Vorbis (FLAC/Ogg) | MP4 |
|---|---|---|---|
| genres | `TCON` (`genre`) | `GENRE` | `©gen` (`genre`) |
| moods | `TMOO` | `MOOD` | — |
| bpm | `TBPM` | `BPM` | `tmpo` |
| key | `TKEY` | `INITIALKEY` | — |
| vocals | `USLT` lyrics → yes; genre "Instrumental" → no | `LYRICS` | — |

These values have the same standing as harvested attribution: they only fill blanks and
are stamped `embedded` in `field_provenance`, so a re-harvest never overwrites a
correction. Instruments and energy have no common tag, so they are typed by hand.

**URL import** carries a site's genre (SoundCloud and Bandcamp report one) onto audio
imports. YouTube's `categories` are left out: they say "Music" for every song.

**By hand,** in the asset's Music tab (audio assets only), or through `PATCH
/api/assets/{id}`, where a list replaces the stored one and `[]` clears it.

## Search

Genres, moods, instruments, "instrumental"/"vocals" and "<level> energy" go into a new
`music_text` column of `asset_fts`, and into the text the asset's embedding is built
from. BPM and key are left out of the text: a bare number collides with titles. Adding
the column means rebuilding the FTS table, which migration `d71e4b9a2c86` does and then
repopulates, as `9c2e08b4a1f7` did for attribution.

A clip does not inherit its parent's profile, unlike attribution. Thirty seconds cut
from a song can be its quiet intro.

## Not done yet

- GVC reading the profile: `search_assets` should return it to the assistant (step 2).
- Analysing the audio itself (BPM, energy, vocals, audio–text embeddings), for files
  whose tags say nothing.
- Range filters (`bpm_min`/`bpm_max`) on `/api/assets`.
