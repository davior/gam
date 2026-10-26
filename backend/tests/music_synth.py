"""Synthesised music with a known tempo and key, for the audio-analysis tests.

Built in code rather than committed as fixtures: what is being tested is whether the
analysis recovers a tempo and a key, and a test that states them in its own source
says what it expects more plainly than a binary file named after them. Seeded, so it
is the same audio on every run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

SAMPLE_RATE = 22050
MAJOR = (0, 2, 4, 5, 7, 9, 11)
MINOR = (0, 2, 3, 5, 7, 8, 10)
PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _tone(freq: float, seconds: float, amp: float, decay: float) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    wave = sum((0.6**h) * np.sin(2 * np.pi * freq * (h + 1) * t) for h in range(5))
    return amp * wave * np.exp(-decay * t)


def _kick() -> np.ndarray:
    t = np.arange(int(0.25 * SAMPLE_RATE)) / SAMPLE_RATE
    sweep = 50 + 100 * np.exp(-30 * t)
    return 0.8 * np.sin(2 * np.pi * np.cumsum(sweep) / SAMPLE_RATE) * np.exp(-12 * t)


def _hit(rng, seconds: float, amp: float, decay: float) -> np.ndarray:
    noise = np.diff(rng.standard_normal(int(seconds * SAMPLE_RATE)), prepend=0.0)
    return amp * noise * np.exp(-decay * np.arange(noise.size) / SAMPLE_RATE)


def _add(buffer: np.ndarray, sound: np.ndarray, at: float) -> None:
    start = max(0, int(at * SAMPLE_RATE))
    end = min(buffer.size, start + sound.size)
    if start < end:
        buffer[start:end] += sound[: end - start]


def _chord(scale, degree: int) -> list[int]:
    return [scale[(degree + k) % 7] + 12 * ((degree + k) // 7) for k in (0, 2, 4)]


def song(bpm: float, tonic: int, mode: str, *, seconds: float = 30.0, drums: bool = True,
         seed: int = 0) -> np.ndarray:
    """A beat (kick, snare, hats), chords, bass and a melody, in one key.

    The minor progression uses a major V (i–iv–V–i), the raised leading tone that makes
    a listener hear minor rather than its relative major. Without it a minor and a major
    progression can share all four chords and the key is genuinely ambiguous.
    """
    rng = np.random.default_rng(seed)
    buffer = np.zeros(int(seconds * SAMPLE_RATE))
    beat = 60.0 / bpm
    scale = MAJOR if mode == "major" else MINOR
    progression = (0, 4, 5, 3) if mode == "major" else (0, 3, 4, 0)
    root = 48 + tonic

    for index in range(int(seconds / beat)):
        at = index * beat
        bar, position = divmod(index, 4)
        if position == 0:
            degree = progression[bar % 4]
            chord = _chord(scale, degree)
            if mode == "minor" and degree == 4:
                chord = [7, 11, 14]
            for note in chord:
                _add(buffer, _tone(_hz(root + 12 + note), beat * 4, 0.08, 0.4), at)
            _add(buffer, _tone(_hz(root - 12 + scale[degree]), beat * 4, 0.15, 0.6), at)
        melody = root + 24 + scale[int(rng.integers(0, 7))]
        _add(buffer, _tone(_hz(melody), beat, 0.05, 3.0), at + rng.normal(0, 0.008))
        if drums:
            if position in (0, 2):
                _add(buffer, _kick(), at + rng.normal(0, 0.008))
            if position in (1, 3):
                _add(buffer, _hit(rng, 0.15, 0.3, 25), at + rng.normal(0, 0.008))
            _add(buffer, _hit(rng, 0.05, 0.08, 60), at)
            _add(buffer, _hit(rng, 0.05, 0.06, 60), at + beat / 2)

    return (buffer / np.max(np.abs(buffer)) * 0.7).astype(np.float32)


def pad(tonic: int, mode: str, *, seconds: float = 30.0, seed: int = 0) -> np.ndarray:
    """Slow sustained chords with no beat at all: ambient, a drone under narration."""
    rng = np.random.default_rng(seed)
    buffer = np.zeros(int(seconds * SAMPLE_RATE))
    scale = MAJOR if mode == "major" else MINOR
    at, index = 0.0, 0
    while at < seconds:
        length = float(rng.uniform(5, 9))
        envelope_t = np.arange(int(length * SAMPLE_RATE)) / SAMPLE_RATE
        envelope = np.minimum(1, np.minimum(envelope_t / 2.0, (length - envelope_t) / 2.0))
        for note in _chord(scale, (0, 5, 3, 4)[index % 4]):
            _add(buffer, _tone(_hz(48 + tonic + 12 + note), length, 0.1, 0.0) * envelope, at)
        at += length - 1.5
        index += 1
    return (buffer / np.max(np.abs(buffer)) * 0.3).astype(np.float32)


def speechlike(*, seconds: float = 30.0, seed: int = 0) -> np.ndarray:
    """Irregular voiced syllables with pauses: what a talk looks like to the analysis."""
    rng = np.random.default_rng(seed)
    buffer = np.zeros(int(seconds * SAMPLE_RATE))
    at = 0.0
    while at < seconds:
        syllable = float(rng.uniform(0.08, 0.3))
        voice = _tone(float(rng.uniform(100, 220)), syllable, 0.2, 4.0)
        _add(buffer, voice * (1 + 0.3 * rng.standard_normal(voice.size)), at)
        at += syllable + float(rng.uniform(0.02, 0.4))
        if rng.random() < 0.15:
            at += float(rng.uniform(0.3, 1.0))
    return (buffer / np.max(np.abs(buffer)) * 0.5).astype(np.float32)


def write_mp3(samples: np.ndarray, target: Path) -> Path:
    """Encode through ffmpeg, so a test goes through the same decode a real upload does."""
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "-",
            "-c:a", "libmp3lame", "-b:a", "96k", str(target),
        ],
        input=samples.astype("<f4").tobytes(),
        check=True,
    )
    return target
