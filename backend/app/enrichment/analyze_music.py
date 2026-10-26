"""Filling a track's tempo, key and energy from its sound.

The measuring is `ingest/audio_analysis.py`; this decides what to measure and what to
keep. The rule is the one the tag harvest follows: fill blanks only. A tag was written
by someone who heard the track, and a person's correction is a correction, so a
measurement never replaces either. That is also what makes the library-wide run safe to
repeat: a second pass finds nothing blank that the first one could fill.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from sqlmodel import Session, col, select

from app.ingest.audio_analysis import (
    AudioAnalysisError,
    MIN_SECONDS,
    MusicAnalysis,
    analyse,
    decode,
)
from app.ingest.filetypes import TYPE_AUDIO
from app.models.asset import Asset
from app.services import assets as asset_service
from app.storage import StorageError, build_storage

logger = logging.getLogger(__name__)

Progress = Callable[..., None]

# Longer than any track a video is likely to want under it. Past this an audio file is a
# recording (a podcast, an interview, a lecture) and analysing it as music would at best
# waste the CPU and at worst file a talk under "high energy".
MAX_TRACK_SECONDS = 20 * 60

# The fields this can measure. Vocals, genres and moods it does not attempt: see the
# module docstring of `ingest/audio_analysis.py`.
MEASURED_FIELDS = ("music_bpm", "music_key", "music_energy")


def analysable(asset: Asset) -> bool:
    """An audio file of its own (not a clip), of a length a song could be.

    An unknown duration is let through: the probe failing says nothing about the
    content, and decoding will fail cleanly if the file is unreadable.
    """
    if asset.asset_type != TYPE_AUDIO or not asset.storage_key:
        return False
    duration = asset.duration_seconds
    return duration is None or MIN_SECONDS <= duration <= MAX_TRACK_SECONDS


def missing_fields(asset: Asset) -> list[str]:
    return [name for name in MEASURED_FIELDS if getattr(asset, name, None) in (None, "")]


def measure(asset: Asset) -> MusicAnalysis:
    storage = build_storage()
    with storage.materialise(asset.storage_key) as path:
        samples = decode(path, asset.duration_seconds)
    return analyse(samples)


def apply(session: Session, asset: Asset, result: MusicAnalysis) -> list[str]:
    """Write what was measured into the fields that are still blank. Returns those."""
    if not result.is_music:
        return []
    measured = {"music_bpm": result.bpm, "music_key": result.key, "music_energy": result.energy}
    blank = set(missing_fields(asset))
    changes = {
        name: value for name, value in measured.items() if value is not None and name in blank
    }
    if changes:
        asset_service.apply_measured_metadata(session, asset, changes)
    return list(changes)


def describe(result: MusicAnalysis) -> str:
    """The activity line: what the track measured as, whether or not it was all kept."""
    if not result.is_music:
        return "No beat or key found, so nothing was filled in"
    parts = []
    if result.bpm is not None:
        parts.append(f"{result.bpm:g} BPM")
    if result.key:
        parts.append(result.key)
    if result.energy:
        parts.append(f"{result.energy} energy")
    return " · ".join(parts)


def run(session: Session, asset: Asset, progress: Progress) -> str:
    """Analyse one track. Returns what to show in the activity feed."""
    if not analysable(asset):
        raise AudioAnalysisError(
            "Only audio files up to 20 minutes long are analysed as music"
        )

    progress("Decoding the audio", 10, "")
    result = measure(asset)
    progress("Measuring tempo, key and energy", 70, "")
    written = apply(session, asset, result)

    detail = describe(result)
    if result.is_music and not written:
        detail += " (everything was already filled in, so nothing changed)"
    return detail


@dataclass
class LibraryResult:
    scanned: int
    filled: int
    not_music: int
    failed: int


def run_library(session: Session, user_id: str, progress: Progress) -> LibraryResult:
    """Analyse every audio file in this library that has a measured field still blank."""
    pending = [
        asset
        for asset in session.exec(
            select(Asset)
            .where(
                Asset.user_id == user_id,
                Asset.asset_type == TYPE_AUDIO,
                col(Asset.storage_key).is_not(None),
            )
            .order_by(col(Asset.upload_date).desc())
        ).all()
        if analysable(asset) and missing_fields(asset)
    ]
    total = len(pending)
    scanned = filled = not_music = failed = 0

    for index, asset in enumerate(pending):
        pct = int(index * 100 / total) if total else 100
        progress("Analysing music", pct, f"{index + 1} of {total} · {asset.name}")
        try:
            result = measure(asset)
        except (AudioAnalysisError, StorageError):
            # A file that will not decode, or that went missing underneath its row, is
            # this file's problem, not a reason to stop the run.
            failed += 1
            continue
        except Exception:  # noqa: BLE001 - one bad file must not end the run
            failed += 1
            logger.warning("Could not analyse %s", asset.id, exc_info=True)
            continue

        scanned += 1
        if not result.is_music:
            not_music += 1
        elif apply(session, asset, result):
            filled += 1

    return LibraryResult(scanned=scanned, filled=filled, not_music=not_music, failed=failed)
