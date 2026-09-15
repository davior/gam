"""The transcribe job: audio out, transcript in, segments stored.

Runs on a worker thread, so it owns its own session and reports progress through the
callback the queue hands it — which is also the cancellation checkpoint.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Callable

from sqlmodel import Session, col, delete, select

from app.clock import utcnow
from app.enrichment import deepgram
from app.enrichment.audio import AudioExtractionError, extract_audio
from app.ingest.filetypes import TYPE_AUDIO, TYPE_VIDEO
from app.models.asset import Asset
from app.models.transcript import TranscriptSegment
from app.models.usage import KIND_STT, UNIT_SECONDS
from app.search import fts
from app.usage import events as usage_events
from app.settings_store import DEEPGRAM_MODEL, load_deepgram_key, get_setting
from app.storage import build_storage

logger = logging.getLogger(__name__)

Progress = Callable[[str, int, str], None]

TRANSCRIBABLE_TYPES = frozenset({TYPE_AUDIO, TYPE_VIDEO})


class TranscriptionError(Exception):
    """Something the user can be told about."""


def can_transcribe(asset: Asset) -> bool:
    return asset.asset_type in TRANSCRIBABLE_TYPES and bool(asset.storage_key)


def run(session: Session, asset: Asset, progress: Progress) -> int:
    """Transcribe one asset. Returns how many segments were stored.

    Existing segments are replaced wholesale, except those a person has edited — a
    re-run should improve a transcript, not silently discard corrections somebody
    typed. That is FR 8.1.3's principle applied per segment.
    """
    if not can_transcribe(asset):
        raise TranscriptionError("Only audio and video assets can be transcribed")

    api_key = load_deepgram_key(session, asset.user_id)
    if not api_key:
        raise TranscriptionError(
            "No Deepgram API key is configured. Add one in Settings to transcribe."
        )

    model = get_setting(session, asset.user_id, DEEPGRAM_MODEL, deepgram.DEFAULT_MODEL)
    storage = build_storage()

    progress("Extracting audio", 5, "")

    with tempfile.TemporaryDirectory(prefix="gam-transcribe-") as workdir:
        audio_path = Path(workdir) / "audio.flac"
        try:
            with storage.materialise(asset.storage_key) as source:
                extract_audio(source, audio_path, asset.duration_seconds)
        except AudioExtractionError as exc:
            raise TranscriptionError(str(exc)) from exc

        size_mb = audio_path.stat().st_size / (1024 * 1024)
        progress("Transcribing", 25, f"{size_mb:.1f} MB of audio")

        try:
            transcript = deepgram.transcribe_file(audio_path, api_key, model=model)
        except deepgram.DeepgramError as exc:
            raise TranscriptionError(str(exc)) from exc

    # Recorded with no cost: Deepgram bills per minute and its rate is not in
    # `usage/pricing.py`, which covers LLM tokens only. Inventing a figure would be worse
    # than none — but omitting the *event* would make a library total that silently
    # excluded transcription read as complete spend, so the seconds are stored and the
    # readout says they are not costed.
    usage_events.record(
        session,
        user_id=asset.user_id,
        asset_id=asset.id,
        kind=KIND_STT,
        units=int(asset.duration_seconds or 0),
        unit_type=UNIT_SECONDS,
        provider="deepgram",
        model=transcript.model or model,
    )

    # Checkpoint before writing: a cancel pressed during a long upstream call should
    # not still land a transcript afterwards.
    progress("Storing", 90, f"{len(transcript.segments)} segments")

    stored = _store_segments(session, asset, transcript)

    # Index what was said, so it is findable the moment the job finishes rather than
    # waiting for some later sweep.
    try:
        rows = session.exec(
            select(TranscriptSegment)
            .where(TranscriptSegment.asset_id == asset.id)
            .order_by(col(TranscriptSegment.idx))
        ).all()
        fts.index_segments(session, asset.id, rows)
    except Exception:  # noqa: BLE001 - a transcript that exists beats one indexed
        logger.warning("Could not index the transcript of %s", asset.id, exc_info=True)

    asset.transcript_status = "done"
    asset.transcript_model = transcript.model
    asset.transcript_language = transcript.language or None
    asset.metadata_modified_date = utcnow()
    session.add(asset)
    session.commit()

    return stored


def _store_segments(session: Session, asset: Asset, transcript: deepgram.Transcript) -> int:
    """Replace the machine-written segments, keeping the hand-edited ones.

    An edited segment is preserved at its original index, and the new segments fill in
    around it. The alternative — dropping everything — would mean a re-run costs a user
    every correction they have made, which is enough to stop them re-running it at all.
    """
    edited = session.exec(
        select(TranscriptSegment)
        .where(TranscriptSegment.asset_id == asset.id, col(TranscriptSegment.edited).is_(True))
        .order_by(col(TranscriptSegment.idx))
    ).all()
    protected_indices = {segment.idx for segment in edited}

    session.exec(
        delete(TranscriptSegment)
        .where(TranscriptSegment.asset_id == asset.id)
        .where(col(TranscriptSegment.edited).is_(False))
    )

    written = 0
    for index, segment in enumerate(transcript.segments):
        if index in protected_indices:
            continue
        session.add(
            TranscriptSegment(
                asset_id=asset.id,
                user_id=asset.user_id,
                idx=index,
                text=segment.text,
                start_time=segment.start,
                end_time=segment.end,
                speaker=segment.speaker,
                words=json.dumps(
                    [{"w": w.text, "s": round(w.start, 3), "e": round(w.end, 3)} for w in segment.words],
                    separators=(",", ":"),
                ),
            )
        )
        written += 1

    session.commit()
    return written + len(edited)
