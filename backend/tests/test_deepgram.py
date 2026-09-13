"""Parsing a Deepgram prerecorded response.

The HTTP call cannot be tested here — it needs a key and a live account — so it is
split out from the parsing, which is the part that actually decides whether a
transcript is usable. The fixture is a real response shape: utterances with
`punctuated_word`, speaker labels and word-level timings.
"""

import json
from pathlib import Path

import httpx
import pytest

from app.enrichment import deepgram

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(name="payload")
def payload_fixture():
    return json.loads((FIXTURES / "deepgram_response.json").read_text())


def test_utterances_become_segments(payload):
    transcript = deepgram.parse_response(payload)

    assert len(transcript.segments) == 2
    assert transcript.segments[0].text.startswith("We are looking at deploying nano weapons")
    assert transcript.segments[1].text == "It is already feasible."


def test_segments_carry_timestamps(payload):
    """The whole point: FR 10.1.4 needs the moment, not just the file."""
    first = deepgram.parse_response(payload).segments[0]

    assert first.start == pytest.approx(0.08)
    assert first.end == pytest.approx(4.32)


def test_word_level_timings_are_kept(payload):
    """What lets search highlight the exact word and seek to it."""
    first = deepgram.parse_response(payload).segments[0]

    assert len(first.words) == 10
    deploying = next(w for w in first.words if w.text == "deploying")
    assert deploying.start == pytest.approx(1.2)
    assert deploying.end == pytest.approx(1.76)


def test_words_use_the_punctuated_form(payload):
    """`punctuated_word` carries the casing and punctuation smart_format added; the
    bare `word` is lowercase and stripped. A transcript should read as written."""
    words = deepgram.parse_response(payload).segments[0].words

    assert words[0].text == "We"        # not "we"
    assert words[-1].text == "dispersion."  # not "dispersion"


def test_speakers_are_kept(payload):
    """Diarisation matters for interviews: who said it is half of finding it again."""
    segments = deepgram.parse_response(payload).segments

    assert segments[0].speaker == 0
    assert segments[1].speaker == 1


def test_empty_utterances_are_dropped(payload):
    """The fixture ends with a whitespace-only utterance, which Deepgram emits for
    silence. A blank segment in the reader is noise."""
    assert all(segment.text.strip() for segment in deepgram.parse_response(payload).segments)


def test_model_comes_from_the_response_not_the_request(payload):
    """A transcript should record what actually ran, not what was asked for."""
    transcript = deepgram.parse_response(payload, requested_model="nova-2")
    assert transcript.model == "nova-3"


def test_detected_language_is_kept(payload):
    assert deepgram.parse_response(payload).language == "en"


def test_falls_back_to_channels_when_there_are_no_utterances(payload):
    """utterances=true is always requested, so this guards an unexpected response
    rather than a normal path — but returning nothing at all would lose a transcript
    that was paid for."""
    del payload["results"]["utterances"]

    transcript = deepgram.parse_response(payload)
    assert len(transcript.segments) == 1
    assert "deploying nano weapons" in transcript.segments[0].text
    assert transcript.segments[0].words


def test_an_empty_response_is_an_empty_transcript_not_a_crash():
    transcript = deepgram.parse_response({})
    assert transcript.segments == []
    assert transcript.language == ""


def test_malformed_timings_do_not_crash_the_parse():
    """A missing or non-numeric timestamp should cost that word its position, not the
    whole transcript."""
    payload = {
        "results": {
            "utterances": [
                {
                    "transcript": "Hello there.",
                    "start": "not a number",
                    "end": None,
                    "words": [{"word": "hello", "start": None, "end": "x"}],
                }
            ]
        }
    }
    segment = deepgram.parse_response(payload).segments[0]
    assert segment.text == "Hello there."
    assert segment.start == 0.0
    assert segment.words[0].text == "hello"


# ─── the HTTP layer's error handling ─────────────────────────────────────────


def _respond(status_code: int, json_body=None, text: str = ""):
    def handler(request: httpx.Request) -> httpx.Response:
        if json_body is not None:
            return httpx.Response(status_code, json=json_body)
        return httpx.Response(status_code, text=text)

    return handler


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, "rejected the API key"),
        (402, "no credit remaining"),
        (500, "Deepgram returned 500"),
    ],
)
def test_http_errors_are_named(status_code, expected, tmp_path, monkeypatch):
    """A user can fix a bad key or a dry account. Both are named rather than buried in
    a status code."""
    audio = tmp_path / "audio.flac"
    audio.write_bytes(b"fake audio")

    def fake_post(*_args, **_kwargs):
        return httpx.Response(status_code, json={"err_msg": "upstream detail"})

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(deepgram.DeepgramError, match=expected):
        deepgram.transcribe_file(audio, "fake-key")


def test_a_timeout_is_reported_as_one(tmp_path, monkeypatch):
    audio = tmp_path / "audio.flac"
    audio.write_bytes(b"fake audio")

    def fake_post(*_args, **_kwargs):
        raise httpx.ReadTimeout("too slow")

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(deepgram.DeepgramError, match="did not respond in time"):
        deepgram.transcribe_file(audio, "fake-key")


def test_a_successful_call_is_parsed(tmp_path, monkeypatch, payload):
    """The one end-to-end path through the client that can be exercised without a key."""
    audio = tmp_path / "audio.flac"
    audio.write_bytes(b"fake audio")

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        captured["headers"] = kwargs.get("headers")
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(httpx, "post", fake_post)

    transcript = deepgram.transcribe_file(audio, "fake-key", model="nova-3")

    assert len(transcript.segments) == 2
    assert captured["url"] == deepgram.LISTEN_URL
    # utterances is what produces segments at all; without it the response is one wall
    # of text per channel and nothing can be jumped to.
    assert captured["params"]["utterances"] == "true"
    assert captured["params"]["diarize"] == "true"
    assert captured["headers"]["Authorization"] == "Token fake-key"
