"""Measuring a track's tempo, key and energy from its sound.

For the files whose tags say nothing, which is most music downloaded from free
libraries. The tags are still the better source when present (whoever wrote them heard
the track), so this only ever fills what they left blank; see
`enrichment/analyze_music.py`.

Plain numpy over ffmpeg-decoded PCM, deliberately. librosa would do all three with less
code, but it brings numba, llvmlite, scipy, soundfile and audioread, which is a long list
of compiled wheels to keep matching the pinned Python 3.13 (the reason requirements.txt
is careful about every compiled dependency). These three measurements are well-known
signal processing that fits in one file.

What it does not attempt, and why:

- **Vocals.** Telling singing from a lead instrument reliably takes a trained model. A
  heuristic that guessed would put songs under narration, the exact failure the
  nullable `music_vocals` exists to prevent.
- **Genre and mood.** Same reason, and the words are the person's or the tagger's.

How good it is: tempo is the dependable one on music with a beat, apart from the
usual octave ambiguity (a 140 BPM track read as 70) which no method escapes. Key is
right more often than not on tonal music and commonly confuses a major key with its
relative minor, which share every note. Energy is a coarse three-way split. Everything
here is a measurement stamped `analysis`, so a person's correction always stands.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from app.media_tools import ffmpeg_available

logger = logging.getLogger(__name__)

SAMPLE_RATE = 22050

# Two minutes is enough to measure a tempo and a key and keeps a long file cheap. Taken
# from past the opening when the file is long enough: intros are often beatless or in
# a different texture, and the body of a track is what a video will play under a scene.
ANALYSIS_SECONDS = 120.0
SKIP_INTRO_SECONDS = 20.0
# Below this there is too little to find a beat period in several repetitions.
MIN_SECONDS = 8.0

# The onset pass: ~11.6 ms per frame (86 frames a second), fine enough that a beat
# period at 180 BPM spans 29 frames.
_ONSET_FFT = 2048
_ONSET_HOP = 256
# The pitch pass needs finer frequency resolution than timing does: 2.7 Hz bins, so
# neighbouring semitones are separate bins down to about C2.
_CHROMA_FFT = 8192
_CHROMA_HOP = 2048
_CHROMA_MIN_HZ = 65.0
_CHROMA_MAX_HZ = 2100.0
# Frames per FFT batch, so two minutes never needs the whole spectrogram in memory.
_BATCH = 512

_MIN_BPM = 50.0
_MAX_BPM = 210.0
# Where the tempo search leans when two octaves score alike. Most music people will put
# under a video sits around here, and a log-normal prior is the usual way to break the
# 70/140 tie (the same choice librosa makes).
_PRIOR_BPM = 110.0
_PRIOR_OCTAVES = 1.0
# How strongly the onset curve must repeat at the beat period to call it a beat at all.
# Below this the track is beatless (ambient, a drone, speech) and no tempo is better
# than a confident wrong one. A slow pad whose chords change every few seconds reached
# 0.24 in testing; a drum pattern sits above 0.6.
_MIN_BEAT_STRENGTH = 0.3

# Krumhansl–Kessler key profiles: how strongly each scale degree is felt in a key.
_MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
# Correlation with the best key's profile needed to name one. Atonal material, noise and
# speech correlate weakly with every key; tonal music sits well above this.
_MIN_KEY_CORRELATION = 0.6
# Without a beat, a key alone has to be clearer than that to call the file music at all.
# Pitched speech reached 0.65 in testing; sustained tonal pads sit above 0.85.
_MIN_BEATLESS_KEY_CORRELATION = 0.75


class AudioAnalysisError(Exception):
    """The file could not be decoded, or is too short to measure."""


@dataclass(frozen=True)
class MusicAnalysis:
    bpm: Optional[float]
    key: Optional[str]
    energy: Optional[str]
    # Kept for the job's detail line and for tests; not stored.
    beat_strength: float = 0.0
    key_correlation: float = 0.0
    energy_score: float = 0.0

    @property
    def is_music(self) -> bool:
        """A beat, or a key clear enough to stand without one. Otherwise this is
        probably speech or noise, and nothing measured here is worth recording."""
        if self.bpm is not None:
            return True
        return self.key is not None and self.key_correlation >= _MIN_BEATLESS_KEY_CORRELATION


# ─── decoding ────────────────────────────────────────────────────────────────


def decode(path: Path, duration_seconds: Optional[float] = None) -> np.ndarray:
    """Mono float32 PCM at `SAMPLE_RATE`, up to `ANALYSIS_SECONDS` of it."""
    if not ffmpeg_available():
        raise AudioAnalysisError("ffmpeg is not available on this server")

    start = 0.0
    if duration_seconds and duration_seconds > ANALYSIS_SECONDS + 2 * SKIP_INTRO_SECONDS:
        start = SKIP_INTRO_SECONDS

    argv = [
        "ffmpeg", "-nostdin", "-v", "error",
        # Before -i, so ffmpeg seeks rather than decoding the intro to discard it.
        "-ss", f"{start:.3f}",
        "-t", f"{ANALYSIS_SECONDS:.3f}",
        "-i", str(path),
        "-vn", "-map", "0:a:0",
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "f32le", "-",
    ]
    try:
        completed = subprocess.run(argv, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired as exc:
        raise AudioAnalysisError("Decoding the audio took too long") from exc
    except OSError as exc:
        raise AudioAnalysisError(f"Could not run ffmpeg: {exc}") from exc

    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", "replace").strip()
        if "does not contain any stream" in stderr or "Stream map" in stderr:
            raise AudioAnalysisError("This file has no audio track to analyse")
        logger.warning("Audio decode failed for %s: %s", path.name, stderr[-400:])
        raise AudioAnalysisError("Could not decode the audio")

    return np.frombuffer(completed.stdout, dtype="<f4").astype(np.float32)


# ─── analysis ────────────────────────────────────────────────────────────────


def analyse(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> MusicAnalysis:
    """Tempo, key and energy of mono PCM. Pure, so it is tested on synthesised audio."""
    samples = np.asarray(samples, dtype=np.float32)
    if samples.size < MIN_SECONDS * sample_rate:
        raise AudioAnalysisError("Too short to measure a tempo or a key")

    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    if rms < 1e-4:
        # Digital silence (-80 dBFS). Nothing to measure, and dividing by it below would
        # turn noise into confident nonsense.
        return MusicAnalysis(bpm=None, key=None, energy=None)

    onset, centroid_hz = _onset_envelope(samples, sample_rate)
    frame_rate = sample_rate / _ONSET_HOP

    bpm, beat_strength = _tempo(onset, frame_rate)
    key, key_correlation = _key(samples, sample_rate)
    energy, energy_score = _energy(rms, centroid_hz, bpm, beat_strength)

    return MusicAnalysis(
        bpm=bpm,
        key=key,
        energy=energy,
        beat_strength=beat_strength,
        key_correlation=key_correlation,
        energy_score=energy_score,
    )


def _frames(samples: np.ndarray, size: int, hop: int):
    """Hann-windowed frames in batches, so memory stays flat however long the input."""
    if samples.size < size:
        samples = np.pad(samples, (0, size - samples.size))
    window = np.hanning(size).astype(np.float32)
    view = np.lib.stride_tricks.sliding_window_view(samples, size)[::hop]
    for start in range(0, view.shape[0], _BATCH):
        yield view[start:start + _BATCH] * window


def _onset_envelope(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, float]:
    """Spectral flux: how much new energy arrives in each frame, summed over frequency.

    Log-compressed first, so a quiet hi-hat counts as an onset next to a loud bass and
    the curve follows rhythm rather than loudness. Also returns the mean spectral
    centroid, which the same magnitudes give for free.
    """
    freqs = np.fft.rfftfreq(_ONSET_FFT, 1.0 / sample_rate).astype(np.float32)
    flux_parts: list[np.ndarray] = []
    centroid_sum = 0.0
    weight_sum = 0.0
    previous: Optional[np.ndarray] = None

    for batch in _frames(samples, _ONSET_FFT, _ONSET_HOP):
        magnitude = np.abs(np.fft.rfft(batch, axis=1)).astype(np.float32)
        energy = magnitude.sum(axis=1)
        centroid_sum += float((magnitude @ freqs).sum())
        weight_sum += float(energy.sum())

        compressed = np.log1p(100.0 * magnitude)
        stacked = compressed if previous is None else np.vstack([previous[None, :], compressed])
        rise = np.maximum(np.diff(stacked, axis=0), 0.0).sum(axis=1)
        if previous is None:
            rise = np.concatenate([[0.0], rise])
        flux_parts.append(rise.astype(np.float32))
        previous = compressed[-1]

    flux = np.concatenate(flux_parts)
    centroid = centroid_sum / weight_sum if weight_sum > 0 else 0.0
    return flux, centroid


def _tempo(onset: np.ndarray, frame_rate: float) -> tuple[Optional[float], float]:
    """The beat period at which the onset curve best repeats itself.

    Autocorrelation of the onset curve, scored on a fine BPM grid with the first four
    multiples of each candidate period (a real beat repeats at 2x and 3x its period too,
    which a single lag misses), then weighted by a prior that settles octave ties.
    """
    # Remove the slow swell of loudness, keeping only the pulses on top of it.
    smoothing = max(int(frame_rate * 0.4), 1)
    trend = np.convolve(onset, np.ones(smoothing) / smoothing, mode="same")
    pulses = np.maximum(onset - trend, 0.0)
    pulses -= pulses.mean()
    norm = float(np.sqrt(np.sum(pulses * pulses)))
    if norm == 0:
        return None, 0.0

    size = 1 << int(np.ceil(np.log2(2 * pulses.size)))
    spectrum = np.fft.rfft(pulses, size)
    acf = np.fft.irfft(spectrum * np.conj(spectrum), size)[: pulses.size]
    acf /= acf[0]

    bpms = np.arange(_MIN_BPM, _MAX_BPM + 0.05, 0.1)
    periods = 60.0 * frame_rate / bpms
    lags = np.arange(acf.size)

    def at(lag: np.ndarray) -> np.ndarray:
        return np.interp(lag, lags, acf, right=0.0)

    comb = at(periods) + 0.5 * at(2 * periods) + 0.33 * at(3 * periods) + 0.25 * at(4 * periods)
    prior = np.exp(-0.5 * (np.log2(bpms / _PRIOR_BPM) / _PRIOR_OCTAVES) ** 2)
    best = int(np.argmax(comb * prior))

    strength = float(at(np.array([periods[best]]))[0])
    if strength < _MIN_BEAT_STRENGTH:
        return None, strength
    return round(float(bpms[best]), 1), strength


def _chroma(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """How much of each of the twelve pitch classes the track contains, overall.

    Each frame is normalised before it is summed, so a long quiet passage counts as much
    as a short loud one: the key is a property of the whole track, not of its peaks.
    """
    freqs = np.fft.rfftfreq(_CHROMA_FFT, 1.0 / sample_rate)
    band = (freqs >= _CHROMA_MIN_HZ) & (freqs <= _CHROMA_MAX_HZ)
    midi = 69.0 + 12.0 * np.log2(freqs[band] / 440.0)
    pitch_class = np.mod(np.round(midi), 12).astype(int)
    # Bins that sit between two semitones (a detuned partial, a bin straddling both)
    # say little about either, so they count for less.
    closeness = 1.0 - 2.0 * np.abs(midi - np.round(midi))
    mapping = np.zeros((band.sum(), 12), dtype=np.float32)
    mapping[np.arange(band.sum()), pitch_class] = np.clip(closeness, 0.0, 1.0)

    total = np.zeros(12, dtype=np.float64)
    for batch in _frames(samples, _CHROMA_FFT, _CHROMA_HOP):
        magnitude = np.abs(np.fft.rfft(batch, axis=1))[:, band].astype(np.float32)
        frames = magnitude @ mapping
        peaks = frames.max(axis=1, keepdims=True)
        total += (frames / np.where(peaks > 0, peaks, 1.0)).sum(axis=0)
    return total


def _key(samples: np.ndarray, sample_rate: int) -> tuple[Optional[str], float]:
    """The key whose Krumhansl–Kessler profile best matches the track's pitch classes."""
    chroma = _chroma(samples, sample_rate)
    if not np.any(chroma):
        return None, 0.0

    best_name: Optional[str] = None
    best_r = -1.0
    for tonic in range(12):
        for profile, mode in ((_MAJOR_PROFILE, "major"), (_MINOR_PROFILE, "minor")):
            r = float(np.corrcoef(chroma, np.roll(profile, tonic))[0, 1])
            if r > best_r:
                best_r = r
                best_name = f"{_PITCH_NAMES[tonic]} {mode}"

    if best_r < _MIN_KEY_CORRELATION:
        return None, best_r
    return best_name, best_r


def _energy(
    rms: float, centroid_hz: float, bpm: Optional[float], beat_strength: float
) -> tuple[str, float]:
    """Low, medium or high, from loudness, brightness and drive.

    A weighted blend rather than any one measure: a loud, slow orchestral swell and a
    quiet, fast hi-hat pattern both read as "medium" to a listener, and either measure
    alone would put one of them at an extreme. The ranges are where mastered music
    actually sits; it is a coarse three-way split and is named as such in the UI.

    Drive comes from the tempo pass rather than from counting onsets. Counted against
    the onset curve's own spread, a beatless pad still produced two "onsets" a second of
    noise, enough to lift ambient music out of "low". A track with no beat has no drive.
    """
    loudness_db = 20.0 * np.log10(max(rms, 1e-9))
    loud = np.clip((loudness_db + 32.0) / 22.0, 0.0, 1.0)      # -32 dBFS .. -10 dBFS
    bright = np.clip((centroid_hz - 600.0) / 2400.0, 0.0, 1.0)  # 600 Hz .. 3 kHz
    if bpm is None:
        drive = 0.0
    else:
        pace = np.clip((bpm - 50.0) / 110.0, 0.0, 1.0)          # 50 .. 160 BPM
        pulse = np.clip(beat_strength / 0.6, 0.0, 1.0)
        drive = 0.5 * pace + 0.5 * pulse

    score = float(0.45 * loud + 0.2 * bright + 0.35 * drive)
    if score < 0.38:
        return "low", score
    if score > 0.62:
        return "high", score
    return "medium", score
