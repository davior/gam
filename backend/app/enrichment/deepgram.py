"""The Deepgram prerecorded speech-to-text client.

gecko-notes integrates Deepgram's *streaming* endpoints (a websocket relay for live
dictation) and its TTS, but not the prerecorded one. This is new: a transcript of an
existing file, with word-level timings, which is what FR 10.1.4 needs to return "the
moment Giordano said it" rather than "the file he said it in".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

LISTEN_URL = "https://api.deepgram.com/v1/listen"

DEFAULT_MODEL = "nova-3"

# A long recording is a long upload followed by a long transcription, and Deepgram
# holds the connection for both. Generous, because failing at minute nine of a
# ten-minute job wastes the whole call and its cost.
REQUEST_TIMEOUT = httpx.Timeout(1800.0, connect=30.0, write=900.0, pool=30.0)


class DeepgramError(Exception):
    """The API refused, failed, or returned something unusable."""


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Segment:
    text: str
    start: float
    end: float
    speaker: Optional[int] = None
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    segments: list[Segment]
    language: str
    model: str

    @property
    def duration_covered(self) -> float:
        return self.segments[-1].end if self.segments else 0.0


def transcribe_file(
    audio_path: Path,
    api_key: str,
    *,
    model: str = DEFAULT_MODEL,
    language: Optional[str] = None,
) -> Transcript:
    """Send one audio file and return its transcript.

    `utterances=true` is what produces sensible segments. Without it Deepgram returns
    one wall of text per channel, which is useless for jumping to a moment — the whole
    point here. `diarize=true` labels speakers, which matters for interviews: "who said
    the thing" is half of finding it again.
    """
    params: dict[str, Any] = {
        "model": model,
        "smart_format": "true",
        "punctuate": "true",
        "utterances": "true",
        "diarize": "true",
    }
    if language:
        params["language"] = language
    else:
        # Let Deepgram pick, rather than assuming English of a library that may not be.
        params["detect_language"] = "true"

    headers = {
        "Authorization": f"Token {api_key}",
        # Deepgram sniffs the container; FLAC is what app.enrichment.audio produces.
        "Content-Type": "audio/flac",
    }

    try:
        with open(audio_path, "rb") as handle:
            response = httpx.post(
                LISTEN_URL,
                params=params,
                headers=headers,
                content=handle,
                timeout=REQUEST_TIMEOUT,
            )
    except httpx.TimeoutException as exc:
        raise DeepgramError("Deepgram did not respond in time") from exc
    except httpx.RequestError as exc:
        raise DeepgramError(f"Could not reach Deepgram: {type(exc).__name__}") from exc
    except OSError as exc:
        raise DeepgramError(f"Could not read the audio file: {exc}") from exc

    if response.status_code == 401:
        # The one failure a user can actually fix, so it is named rather than buried in
        # a status code.
        raise DeepgramError("Deepgram rejected the API key")
    if response.status_code == 402:
        raise DeepgramError("The Deepgram account has no credit remaining")
    if response.status_code >= 400:
        detail = _error_detail(response)
        raise DeepgramError(f"Deepgram returned {response.status_code}: {detail}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise DeepgramError("Deepgram returned a response that was not JSON") from exc

    return parse_response(payload, requested_model=model)


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return (response.text or "").strip()[:200] or "no detail"
    if isinstance(body, dict):
        return str(body.get("err_msg") or body.get("message") or body)[:200]
    return str(body)[:200]


def parse_response(payload: dict[str, Any], *, requested_model: str = DEFAULT_MODEL) -> Transcript:
    """Turn Deepgram's response into segments.

    Split out from the HTTP call so the shape can be tested against a recorded
    response without a network or an API key — which is the only part of this a test
    can meaningfully assert.
    """
    results = payload.get("results") or {}
    metadata = payload.get("metadata") or {}

    model = requested_model
    # Deepgram reports what it actually ran under a uuid key; the name is what matters.
    for info in (metadata.get("model_info") or {}).values():
        if isinstance(info, dict) and info.get("name"):
            model = str(info["name"])
            break

    language = _detected_language(results)

    utterances = results.get("utterances")
    if utterances:
        segments = [_segment_from_utterance(u) for u in utterances if _has_text(u)]
    else:
        # utterances=true was requested, so this is a fallback for an unexpected
        # response rather than a normal path: one segment per channel alternative.
        segments = _segments_from_channels(results)

    return Transcript(segments=segments, language=language, model=model)


def _has_text(utterance: Any) -> bool:
    return isinstance(utterance, dict) and bool((utterance.get("transcript") or "").strip())


def _segment_from_utterance(utterance: dict[str, Any]) -> Segment:
    words = [
        Word(
            # punctuated_word carries the casing and punctuation smart_format added;
            # `word` is the bare token. The transcript should read as written.
            text=str(w.get("punctuated_word") or w.get("word") or ""),
            start=_float(w.get("start")),
            end=_float(w.get("end")),
        )
        for w in utterance.get("words") or []
        if isinstance(w, dict)
    ]

    speaker = utterance.get("speaker")
    return Segment(
        text=str(utterance.get("transcript") or "").strip(),
        start=_float(utterance.get("start")),
        end=_float(utterance.get("end")),
        speaker=int(speaker) if isinstance(speaker, (int, float)) else None,
        words=words,
    )


def _segments_from_channels(results: dict[str, Any]) -> list[Segment]:
    segments: list[Segment] = []
    for channel in results.get("channels") or []:
        for alternative in (channel or {}).get("alternatives") or []:
            text = str(alternative.get("transcript") or "").strip()
            if not text:
                continue
            words = [
                Word(
                    text=str(w.get("punctuated_word") or w.get("word") or ""),
                    start=_float(w.get("start")),
                    end=_float(w.get("end")),
                )
                for w in alternative.get("words") or []
                if isinstance(w, dict)
            ]
            segments.append(
                Segment(
                    text=text,
                    start=words[0].start if words else 0.0,
                    end=words[-1].end if words else 0.0,
                    words=words,
                )
            )
            break  # the first alternative is the best one
    return segments


def _detected_language(results: dict[str, Any]) -> str:
    for channel in results.get("channels") or []:
        language = (channel or {}).get("detected_language")
        if language:
            return str(language)
    return ""


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
