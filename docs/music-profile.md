# Music profile

**Status:** built, with analysis of the sound. Step 1 of making the library useful as a soundtrack source for Gecko
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

**The file's own tags,** on upload and in the "Read file tags" library run
(`harvest_attribution`, Settings → Fill in metadata), audio only. Read from the ffprobe tag block `probe.py` already
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

**Measured from the sound** (`backend/app/ingest/audio_analysis.py`), for files whose
tags say nothing. Tempo, key and energy only. Queued after every audio upload of a
song's length (8 s to 20 min), runnable per track from the Music tab, and across the
library from Settings → Fill in metadata. It fills blanks only, after the tags have been
read, and is stamped `analysis` in `field_provenance`, a fourth value beside human, ai and
embedded: no model guessed it and nobody wrote it into the file.

| Measure | How | How good |
|---|---|---|
| Tempo | Autocorrelation of a log spectral-flux onset curve, scored over 50–210 BPM with its first four multiples, weighted by a log-normal prior at 110 BPM | Exact on synthetic beats from 92 to 150 BPM. Very slow or fast tracks can read an octave off (72 as 144, 170 as 85), the ambiguity every method has |
| Key | Pitch-class profile (8192-point FFT, 65 Hz–2.1 kHz, each frame normalised) correlated with the 24 Krumhansl–Kessler profiles | Exact on synthetic progressions with a harmonic-minor V; a progression that shares all its chords with its relative major is reported as either, which is what it is |
| Energy | Blend of loudness (−32…−10 dBFS), spectral centroid (0.6–3 kHz) and drive (tempo and beat strength; none without a beat) | Coarse by design: three levels |

Two gates keep it from describing things that are not music. A tempo needs a beat
strength of 0.3 (a slow pad whose chords change every few seconds reached 0.24). Without
a beat, a key needs a correlation of 0.75 to count as music (pitched speech reached
0.65). When neither is found, nothing is written, which is what a talk or a podcast gets.

Plain numpy over ffmpeg-decoded PCM, with no new dependency: librosa would bring numba,
llvmlite, scipy and soundfile, a lot of compiled wheels to keep matching the pinned
Python. It reads two minutes from past a 20-second intro and takes about a second.

Not measured: vocals (telling singing from a lead instrument takes a trained model, and
a guess would put songs under narration), genre and mood.

**By hand,** in the asset's Music tab (audio assets only), or through `PATCH
/api/assets/{id}`, where a list replaces the stored one and `[]` clears it.

## Audio kind

`audio_kind`: `music`, `speech`, `sfx`, or NULL for not known. It decides what happens to
an audio upload:

| Kind | Set by | Transcribed on upload | Analysed for tempo/key/energy |
|---|---|---|---|
| music | A genre or BPM tag; a music page import; by hand | No | Yes |
| speech | A speech genre tag (Podcast, Speech, Spoken Word, Audiobook…); by hand | Yes | No |
| sfx | By hand | No | No |
| not known | Anything else | **Yes**, as before the kind existed | Yes |

Unknown is transcribed on purpose: a voice recording losing its transcript is the costly
mistake, so only a confident kind skips it. The kind is never guessed from the sound,
because a podcast over a music bed has a beat and a key. It is decided at upload, from
tags or from the importer, because transcription is queued the moment a file lands.

Changing it in the Music tab ("This is") catches up: marked speech, an untranscribed
file is transcribed; marked music, it is measured. Nothing is undone, and the Transcribe
button works on any kind. Video Creator reads it to offer music, not voice memos, as a
soundtrack.

## Search

Genres, moods, instruments, "instrumental"/"vocals" and "<level> energy" go into a new
`music_text` column of `asset_fts`, and into the text the asset's embedding is built
from. BPM and key are left out of the text: a bare number collides with titles. Adding
the column means rebuilding the FTS table, which migration `d71e4b9a2c86` does and then
repopulates, as `9c2e08b4a1f7` did for attribution.

A clip does not inherit its parent's profile, unlike attribution. Thirty seconds cut
from a song can be its quiet intro.

## Not done yet

- Vocals detection, and audio–text embeddings (CLAP) so a track can be found by how it
  sounds rather than by the words on it. Both need a trained model, and so torch.
- Range filters (`bpm_min`/`bpm_max`) on `/api/assets`.
