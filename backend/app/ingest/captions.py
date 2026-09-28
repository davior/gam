"""A platform's captions, turned into transcript segments.

The free fallback for URL imports when no Deepgram key is configured (the user's call,
recorded in docs/url-import.md): a video whose captions are stored as its transcript is
searchable by what was said the moment it lands, rather than never. Deepgram stays the
better transcript — diarised, word-timed, punctuated — and a later run replaces these
rows exactly as it would replace an earlier Deepgram transcript.

Pure: text in, `deepgram.Segment`s out. Reusing that dataclass rather than inventing a
caption-shaped twin is what lets `transcribe.store_transcript` write both.

VTT only. yt-dlp converts whatever a site serves (SRT, TTML, json3) to VTT before this
sees it, so one parser covers every extractor.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Optional

from app.enrichment.deepgram import Segment

# `00:01:02.500 --> 00:01:05.000 align:start position:0%` — hours optional, and a comma
# accepted as well as a dot, since a converter that slipped SRT through would use one.
_CUE_TIMING = re.compile(
    r"^\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})\s+-->\s+((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})"
)

# Inline markup: YouTube's per-word karaoke timestamps (`<00:00:01.200>`), `<c>` spans,
# voice tags, italics. None of it is speech.
_TAG = re.compile(r"<[^>]*>")

# A line that is nothing but a sound annotation — `[Music]`, `[Applause]`, `(laughs)`.
# Kept out because it is not what anybody said: it would match a search for "music" in
# every video with a sting, and pad a summary with stage directions.
_SOUND_ONLY = re.compile(r"^[\[(][^\])]*[\])]$")

# A segment is closed at the end of a sentence, at a pause, or when it has run long.
# Manual captions are punctuated, so the first rule produces sentence-sized rows like
# Deepgram's utterances. Auto-captions mostly are not, and the other two keep a search
# snippet from being a two-minute wall of text.
_SENTENCE_END = re.compile(r"[.?!…][\"')\]]*$")
MAX_SEGMENT_SECONDS = 20.0
PAUSE_SECONDS = 2.0


@dataclass
class _Line:
    text: str
    start: float
    end: float


def parse_vtt(text: str) -> list[Segment]:
    """Segments from a WebVTT document, in order. Empty for anything unreadable."""
    return _merge(_lines(text or ""))


def _lines(text: str) -> list[_Line]:
    """Every distinct spoken line, with the span over which it was on screen.

    The deduplication is the part that matters. YouTube's auto-caption VTT is a rolling
    two-line display: each cue repeats the line before it and adds the next, and a
    ten-millisecond cue in between shows the finished line alone. Read naively, every
    sentence appears two or three times. Comparing against the last line kept drops the
    repeats and extends that line's end time instead, which is what the viewer saw.
    """
    kept: list[_Line] = []
    start: Optional[float] = None
    end = 0.0

    for raw in text.splitlines():
        timing = _CUE_TIMING.match(raw)
        if timing:
            start, end = _seconds(timing.group(1)), _seconds(timing.group(2))
            continue
        if not raw.strip():
            # A blank line ends the cue. Whatever comes before the next timing line is
            # the header, a NOTE or STYLE block, or a cue identifier — numbered cues are
            # common, and without this "2" and "3" would be read as things people said.
            # YouTube's whitespace-only filler lines land here too, which is harmless:
            # nothing follows them inside the same cue.
            start = None
            continue
        if start is None:
            continue
        line = _clean(raw)
        if not line:
            continue
        if kept and kept[-1].text == line:
            kept[-1].end = max(kept[-1].end, end)
            continue
        kept.append(_Line(text=line, start=start, end=end))

    return kept


def _merge(lines: list[_Line]) -> list[Segment]:
    segments: list[Segment] = []
    current: list[_Line] = []

    def close() -> None:
        if current:
            segments.append(
                Segment(
                    text=" ".join(line.text for line in current),
                    start=round(current[0].start, 3),
                    end=round(max(line.end for line in current), 3),
                )
            )
            current.clear()

    for line in lines:
        if current and line.start - current[-1].end > PAUSE_SECONDS:
            close()
        current.append(line)
        spoken = current[-1].end - current[0].start
        if _SENTENCE_END.search(line.text) or spoken >= MAX_SEGMENT_SECONDS:
            close()
    close()

    return segments


def _clean(raw: str) -> str:
    line = html.unescape(_TAG.sub("", raw)).replace(" ", " ")
    line = " ".join(line.split())
    if _SOUND_ONLY.match(line):
        return ""
    return line


def _seconds(stamp: str) -> float:
    parts = stamp.replace(",", ".").split(":")
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total
