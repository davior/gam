"""What a piece of music sounds like, so a video can find a soundtrack that fits it.

Attribution says whose work a track is. This says what it is *for*: a tense, slow,
instrumental piano piece is the answer to "music under the reveal", and nothing in a
track's name or credit says any of that. Gecko Video Creator searches the library for
soundtracks, and without these fields it is choosing by title.

The fields are separate columns rather than one JSON blob, for the same reason the
attribution fields are: `field_provenance` records who wrote each *column*, and a BPM
read out of the file's ID3 frame and a mood a person typed are different kinds of claim.
One blob would collapse them into a single stamp.

Pure, like `attribution.py`: no session, no I/O, so the parsing rules can be tested
exhaustively without a database.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

# Every music field, in one place, so the harvester, the schemas, the read model and the
# index builder cannot drift apart.
MUSIC_FIELDS: tuple[str, ...] = (
    "music_genres",
    "music_moods",
    "music_instruments",
    "music_bpm",
    "music_key",
    "music_vocals",
    "music_energy",
)

# Stored as JSON-as-TEXT lists of short terms.
MUSIC_LIST_FIELDS: tuple[str, ...] = ("music_genres", "music_moods", "music_instruments")

# Three levels rather than a 1-10 score: a person can tell "low" from "high" reliably by
# ear, and nobody can tell a 6 from a 7. A scale finer than anyone can judge would fill
# up with numbers that look precise and mean nothing.
ENERGY_LEVELS: tuple[str, ...] = ("low", "medium", "high")

# Outside this range a "BPM" is a mis-tag (a track number, a year, a bitrate), not a
# tempo. Real music sits well inside it; the margins cover half- and double-time readings.
MIN_BPM = 20.0
MAX_BPM = 300.0

MAX_TERMS = 20
MAX_TERM_LENGTH = 60
# Long enough for "F# minor", "Abm", Camelot "12B" or Open Key "10d"; anything longer is
# a comment that landed in the wrong frame.
MAX_KEY_LENGTH = 16

# ID3v2.3 writes a genre as a reference into the ID3v1 table, "(17)" or "(17)Rock".
# ffprobe resolves most of these to names, but a bare reference can still come through
# and "(17)" is not a genre anybody searches for.
_ID3V1_GENRE_REF = re.compile(r"^\(\d+\)")
# How tag writers separate several values in one frame: ID3v2.4 uses NUL, which ffprobe
# turns into ";", Vorbis comments and taggers commonly use ";" "/" or ",".
_TERM_SEPARATORS = re.compile(r"[;/,\x00]")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def clean_terms(values: Iterable[Any]) -> list[str]:
    """Whitespace collapsed, case-insensitive duplicates and over-long terms dropped.

    The same treatment `ingest/ytdlp.py` gives uploader tags, and for the same reason:
    these come from many writers, and "Ambient" and "ambient " are one genre.
    """
    seen: set[str] = set()
    terms: list[str] = []
    for value in values:
        term = " ".join(value.split()) if isinstance(value, str) else ""
        if not term or len(term) > MAX_TERM_LENGTH or term.lower() in seen:
            continue
        seen.add(term.lower())
        terms.append(term)
        if len(terms) >= MAX_TERMS:
            break
    return terms


def split_terms(raw: Any) -> list[str]:
    """Several values out of one tag string: "Ambient; Cinematic" is two genres."""
    if not isinstance(raw, str):
        return []
    parts = (_ID3V1_GENRE_REF.sub("", part).strip() for part in _TERM_SEPARATORS.split(raw))
    return clean_terms(parts)


def encode_terms(terms: Optional[Iterable[str]]) -> Optional[str]:
    """The column value for a list: JSON, or NULL when there is nothing in it.

    NULL rather than "[]" so "never described" stays one value. The harvest only fills
    blanks, and an empty list stored as text would read as an answer.
    """
    cleaned = clean_terms(terms or [])
    return json.dumps(cleaned) if cleaned else None


def decode_terms(stored: Optional[str]) -> list[str]:
    """A list column as a list. A malformed value reads as empty rather than raising,
    because one corrupt row must not break every listing that includes it."""
    if not stored:
        return []
    try:
        value = json.loads(stored)
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    return [term for term in value if isinstance(term, str)]


def parse_bpm(raw: Any) -> Optional[float]:
    """A tempo out of whatever a tag writer put there: "92", "92.5", "120 BPM", "128,0".

    None for anything outside a plausible tempo. A wrong BPM is worse than none, because
    it ranks a 70 BPM ballad as a match for a 140 BPM cut.
    """
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
    elif isinstance(raw, str):
        match = _NUMBER.search(raw)
        if not match:
            return None
        value = float(match.group().replace(",", "."))
    else:
        return None
    if not MIN_BPM <= value <= MAX_BPM:
        return None
    return round(value, 1)


def clean_key(raw: Any) -> Optional[str]:
    """A musical key as written, trimmed. Not normalised to one notation.

    "Am", "A minor", Camelot "8A" and Open Key "1m" all name the same key, and mapping
    between them is easy to get subtly wrong. Keeping the writer's own notation is
    honest about what the file said; a person can still read and correct it.
    """
    if not isinstance(raw, str):
        return None
    value = " ".join(raw.split())
    if not value or len(value) > MAX_KEY_LENGTH:
        return None
    return value


def to_columns(changes: dict) -> dict:
    """An API payload's music fields in column form: lists become JSON text.

    Everything else passes through untouched, so a PATCH body can go through this whole.
    """
    converted = dict(changes)
    for name in MUSIC_LIST_FIELDS:
        if name in converted:
            converted[name] = encode_terms(converted[name])
    return converted


def index_text(asset: Any) -> str:
    """The music words that go into `asset_fts` and the asset's embedding.

    BPM and key are left out, for the reason `attribution.ATTRIBUTION_TEXT_FIELDS` leaves
    out dates: a bare number in a free-text index collides with titles ("Track 92")
    instead of helping. Vocals and energy are written as the words a person would search
    with, "instrumental" and "high energy", rather than as the stored values.
    """
    parts: list[str] = []
    for name in MUSIC_LIST_FIELDS:
        parts.extend(decode_terms(getattr(asset, name, None)))

    vocals = getattr(asset, "music_vocals", None)
    if vocals is True:
        parts.append("vocals")
    elif vocals is False:
        parts.append("instrumental")

    energy = getattr(asset, "music_energy", None)
    if energy in ENERGY_LEVELS:
        parts.append(f"{energy} energy")

    return " ".join(parts)
