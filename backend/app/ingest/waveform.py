"""The shape of a recording, for drawing a waveform the player can be scrubbed on.

Computed here rather than in the browser. wavesurfer.js will happily decode a file
itself, but it does so by downloading all of it and holding the decoded PCM in memory
— for a ninety-minute interview that is the whole file over the wire before anything
draws, and the better part of a gigabyte of float32 in the tab. A couple of thousand
numbers from the server draw the same picture.

Streamed off ffmpeg's stdout in chunks, reduced as it arrives, so memory stays flat
however long the file is. The duration is taken from the samples actually decoded,
not from the row: a probe can be missing or a little off, and peaks laid out against
the wrong length put every bar slightly away from the moment it represents.

Everything degrades. A waveform is a convenience, like a thumbnail; every failure here
returns None and the player falls back to a flat line that still scrubs.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from app.media_tools import ffmpeg_available

logger = logging.getLogger(__name__)

# Low, deliberately. A waveform is an envelope, and 8 kHz still resolves it to well
# under a millisecond while decoding a quarter as many samples as 22.05 kHz would.
SAMPLE_RATE = 8000

# The first pass keeps one peak per 10 ms block. That is fine enough for a two-second
# clip still to have a couple of hundred bars, and coarse enough that ninety minutes is
# 540k floats (~2 MB) before the final reduction.
BLOCK_SAMPLES = 80

# What the browser gets. More than any panel is wide in bars, few enough to be a small
# JSON body and a cheap cache file.
DEFAULT_BINS = 2000

# 64 KiB of f32le per read: 2 s of audio, a multiple of the 4-byte sample size.
_READ_BYTES = 64 * 1024

# Decoding at 8 kHz runs at hundreds of times real time, so this is reached only by a
# file that is broken or enormous — and then a flat line is the right answer anyway.
TIMEOUT_SECONDS = 600


@dataclass
class Waveform:
    duration: float
    peaks: list[float]


def compute(path: Path, bins: int = DEFAULT_BINS) -> Optional[Waveform]:
    """Peak amplitudes in 0..1, at most `bins` of them, or None if none can be had."""
    if not ffmpeg_available():
        return None
    try:
        return _compute(path, bins)
    except Exception:  # noqa: BLE001 - a waveform must never fail the request
        logger.warning("Could not compute a waveform for %s", path.name, exc_info=True)
        return None


def _compute(path: Path, bins: int) -> Optional[Waveform]:

    argv = [
        "ffmpeg", "-nostdin", "-v", "error",
        "-i", str(path),
        "-vn", "-map", "0:a:0",
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "f32le", "-",
    ]
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        logger.warning("Could not run ffmpeg for a waveform: %s", exc)
        return None

    # A timer rather than a deadline checked in the loop: a read that blocks forever on
    # a wedged decoder would never get back to the check.
    timer = threading.Timer(TIMEOUT_SECONDS, proc.kill)
    timer.start()
    blocks: list[np.ndarray] = []
    carry = np.empty(0, dtype=np.float32)
    total_samples = 0
    try:
        assert proc.stdout is not None
        # ffmpeg's stderr is drained on another thread so a chatty decoder cannot fill
        # its pipe and stall the stdout this loop is waiting on.
        stderr_chunks: list[bytes] = []
        drain = threading.Thread(
            target=lambda: stderr_chunks.append(proc.stderr.read() if proc.stderr else b""),
            daemon=True,
        )
        drain.start()

        pending = b""
        while chunk := proc.stdout.read(_READ_BYTES):
            pending += chunk
            usable = len(pending) - len(pending) % 4
            samples = np.frombuffer(pending[:usable], dtype="<f4")
            pending = pending[usable:]
            total_samples += samples.size

            samples = np.concatenate((carry, np.abs(samples)))
            whole = samples.size - samples.size % BLOCK_SAMPLES
            if whole:
                blocks.append(samples[:whole].reshape(-1, BLOCK_SAMPLES).max(axis=1))
            carry = samples[whole:]

        returncode = proc.wait()
        drain.join(timeout=5)
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    if returncode != 0:
        stderr = b"".join(stderr_chunks).decode("utf-8", "replace").strip()
        logger.warning("Waveform decode failed for %s: %s", path.name, stderr[-400:])
        return None
    if total_samples == 0:
        return None

    if carry.size:
        blocks.append(np.array([carry.max()], dtype=np.float32))
    envelope = np.concatenate(blocks) if blocks else np.zeros(1, dtype=np.float32)

    return Waveform(
        duration=round(total_samples / SAMPLE_RATE, 3),
        peaks=_reduce(envelope, bins),
    )


def _reduce(envelope: np.ndarray, bins: int) -> list[float]:
    """Max-pool down to `bins` and scale so the loudest bar is full height.

    Max rather than mean so a short transient still shows: averaged into its bucket a
    clap disappears, and a clap is exactly what someone scrubbing is looking for.
    Normalised because speech recorded at a sensible level peaks around a fifth of
    full scale, which drawn as-is is a nearly flat line.
    """
    if envelope.size > bins:
        edges = np.linspace(0, envelope.size, bins + 1).astype(int)
        envelope = np.maximum.reduceat(envelope, edges[:-1])

    loudest = float(envelope.max()) if envelope.size else 0.0
    if loudest > 0:
        envelope = envelope / loudest
    return [round(float(v), 3) for v in np.clip(envelope, 0.0, 1.0)]
