"""Turning a site's VTT captions into transcript segments (`ingest/captions.py`)."""

import pytest

from app.ingest.captions import MAX_SEGMENT_SECONDS, parse_vtt

# The shape YouTube's auto-captions actually arrive in: a rolling two-line display where
# each cue repeats the previous line, per-word karaoke timestamps inside `<c>` spans, and
# a 10ms cue between them showing the finished line alone.
ROLLING_AUTO_CAPTIONS = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.500 align:start position:0%
we<00:00:00.400><c> are</c><00:00:00.800><c> talking</c><00:00:01.200><c> about</c>

00:00:02.500 --> 00:00:02.510 align:start position:0%
we are talking about
\x20

00:00:02.510 --> 00:00:05.000 align:start position:0%
we are talking about
nano<00:00:03.000><c> weapons</c><00:00:03.500><c> today</c>

00:00:05.000 --> 00:00:05.010 align:start position:0%
nano weapons today
\x20

00:00:05.010 --> 00:00:07.000 align:start position:0%
nano weapons today
and<00:00:05.500><c> their</c><00:00:06.000><c> deployment</c>
"""


def test_rolling_auto_captions_are_read_once_each():
    """The whole reason the parser deduplicates. Read naively, every line of an
    auto-captioned video appears two or three times — a transcript that says everything
    twice, and a search snippet that repeats itself."""
    segments = parse_vtt(ROLLING_AUTO_CAPTIONS)
    text = " ".join(s.text for s in segments)

    assert text == "we are talking about nano weapons today and their deployment"
    assert text.count("nano weapons") == 1


def test_a_repeated_line_keeps_its_first_start_and_its_last_end():
    segments = parse_vtt(ROLLING_AUTO_CAPTIONS)

    assert segments[0].start == 0.0
    assert segments[-1].end == 7.0


def test_inline_markup_and_entities_are_not_speech():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:03.000
<v Speaker>Tom &amp; <i>Jerry</i> &gt; everyone.</v>
"""
    [segment] = parse_vtt(vtt)
    assert segment.text == "Tom & Jerry > everyone."


def test_sound_annotations_are_dropped():
    """`[Music]` is not what anybody said, and indexing it makes every video with a
    sting match a search for music."""
    vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
[Music]

00:00:02.000 --> 00:00:04.000
Welcome back.

00:00:04.000 --> 00:00:05.000
(applause)
"""
    assert [s.text for s in parse_vtt(vtt)] == ["Welcome back."]


def test_punctuated_captions_split_at_sentences():
    vtt = """WEBVTT

1
00:00:00.000 --> 00:00:02.000
This is the first sentence.

2
00:00:02.000 --> 00:00:03.000
And here is

3
00:00:03.000 --> 00:00:04.500
the second one?
"""
    segments = parse_vtt(vtt)
    assert [s.text for s in segments] == [
        "This is the first sentence.",
        "And here is the second one?",
    ]
    assert (segments[1].start, segments[1].end) == (2.0, 4.5)


def test_unpunctuated_captions_are_cut_before_they_run_on():
    """Auto-captions mostly lack punctuation, so without a length cap the whole video
    would be one segment — one search hit with one timestamp for an hour of speech."""
    cues = "\n\n".join(
        f"00:00:{i * 4:02d}.000 --> 00:00:{i * 4 + 4:02d}.000\nwords without an ending {i}"
        for i in range(12)
    )
    segments = parse_vtt(f"WEBVTT\n\n{cues}\n")

    assert len(segments) > 1
    for segment in segments:
        assert segment.end - segment.start <= MAX_SEGMENT_SECONDS + 4


def test_a_pause_starts_a_new_segment():
    vtt = """WEBVTT

00:00:00.000 --> 00:00:01.000
before the break

00:00:10.000 --> 00:00:11.000
after the break
"""
    assert [s.text for s in parse_vtt(vtt)] == ["before the break", "after the break"]


def test_hours_and_comma_decimals_are_understood():
    vtt = """WEBVTT

01:02:03,500 --> 01:02:05,000
Late in a long interview.
"""
    [segment] = parse_vtt(vtt)
    assert segment.start == pytest.approx(3723.5)
    assert segment.end == pytest.approx(3725.0)


@pytest.mark.parametrize("text", ["", "WEBVTT\n", "not a caption file at all", "WEBVTT\n\nNOTE only a note\n"])
def test_nothing_readable_is_no_segments(text):
    assert parse_vtt(text) == []


def test_segments_carry_no_invented_word_timings():
    """Captions time lines, not words. An empty list is honest; spreading a line's
    duration evenly over its words would put click-to-seek a second or two off."""
    [segment] = parse_vtt("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello there.\n")
    assert segment.words == []
    assert segment.speaker is None
