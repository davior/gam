"""The transcribe job: audio out, transcript in, segments stored.

Runs on a worker thread, so it owns its own session and reports progress through the
callback the queue hands it — which is also the cancellation checkpoint.
"""

from __future__ import annotations

import json
import logging
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Callable

from gecko_suite_client import SuiteError
from sqlmodel import Session, col, delete, select

from app import suite
from app.clock import utcnow
from app.enrichment import deepgram
from app.enrichment.audio import AudioExtractionError, extract_audio, split_audio
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

# How much audio goes in one request. Half an hour of 16 kHz mono FLAC is a few tens of
# MB, far inside the suite's 300 MB cap, and Deepgram answers it in a minute or two. A
# whole long recording in one request is what failed with a 413 (`audio.split_audio`).
PART_SECONDS = 30 * 60


class TranscriptionError(Exception):
    """Something the user can be told about."""


def can_transcribe(asset: Asset) -> bool:
    return asset.asset_type in TRANSCRIBABLE_TYPES and bool(asset.storage_key)


def can_auto_transcribe(session: Session, user_id: str) -> bool:
    """Whether a transcription started now would find a Deepgram key.

    The one question the upload chain and the URL import both ask before doing work that
    would otherwise fail. On the suite the key is the suite's, so it is asked there.
    """
    if suite.enabled():
        return suite.transcription_ready(user_id)
    return bool(load_deepgram_key(session, user_id))


def _transcribe_on_suite(user_id: str, audio_path: Path, model: str, subject: str) -> deepgram.Transcript:
    """The suite's transcript, as the same `Transcript` Deepgram's client returns, so
    storing it is the code that already exists."""
    try:
        connection, speech = suite.speech(user_id, timeout=suite.TRANSCRIBE_TIMEOUT)
        with closing(connection):
            result = speech.transcribe(audio_path.read_bytes(), content_type="audio/flac",
                                       model=model, subject_ref=subject)
    except SuiteError as exc:
        raise TranscriptionError(suite.sentence(exc)) from exc
    except OSError as exc:
        raise TranscriptionError(f"Could not read the audio file: {exc}") from exc
    return deepgram.Transcript(
        segments=[
            deepgram.Segment(
                text=s.text, start=s.start, end=s.end, speaker=s.speaker,
                words=[deepgram.Word(text=w.text, start=w.start, end=w.end) for w in s.words])
            for s in result.segments],
        language=result.language, model=result.model)


def join_parts(pieces: list[tuple[deepgram.Transcript, float]]) -> deepgram.Transcript:
    """One transcript from the parts, each part's times moved by where it starts.

    Speaker numbers are per request (Deepgram diarizes each part on its own), so speaker 0
    in one part need not be speaker 0 in the next. They are kept as given rather than
    guessed at: a wrong match would be worse than a renumbering a person can see.
    """
    if len(pieces) == 1 and pieces[0][1] == 0:
        return pieces[0][0]
    segments: list[deepgram.Segment] = []
    for piece, offset in pieces:
        for s in piece.segments:
            segments.append(deepgram.Segment(
                text=s.text, start=s.start + offset, end=s.end + offset, speaker=s.speaker,
                words=[deepgram.Word(text=w.text, start=w.start + offset, end=w.end + offset)
                       for w in s.words]))
    language = next((p.language for p, _ in pieces if p.language), "")
    model = next((p.model for p, _ in pieces if p.model), "")
    return deepgram.Transcript(segments=segments, language=language, model=model)


def run(session: Session, asset: Asset, progress: Progress) -> int:
    """Transcribe one asset. Returns how many segments were stored.

    Existing segments are replaced wholesale, except those a person has edited — a
    re-run should improve a transcript, not silently discard corrections somebody
    typed. That is FR 8.1.3's principle applied per segment.
    """
    if not can_transcribe(asset):
        raise TranscriptionError("Only audio and video assets can be transcribed")

    # On the suite the key is the suite's, and it says so itself if there is none.
    api_key = None if suite.enabled() else load_deepgram_key(session, asset.user_id)
    if not suite.enabled() and not api_key:
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
            parts = split_audio(audio_path, Path(workdir), PART_SECONDS, asset.duration_seconds)
        except AudioExtractionError as exc:
            raise TranscriptionError(str(exc)) from exc

        pieces: list[tuple[deepgram.Transcript, float]] = []
        for index, (part_path, offset) in enumerate(parts):
            if len(parts) > 1:
                # Also the cancellation checkpoint: a cancel stops between parts rather
                # than after the whole recording has been paid for.
                progress("Transcribing", 25 + (60 * index) // len(parts),
                         f"part {index + 1} of {len(parts)}")
            if suite.enabled():
                piece = _transcribe_on_suite(asset.user_id, part_path, model,
                                             f"gam:asset:{asset.id}")
            else:
                try:
                    piece = deepgram.transcribe_file(part_path, api_key, model=model)
                except deepgram.DeepgramError as exc:
                    raise TranscriptionError(str(exc)) from exc
            pieces.append((piece, offset))
        transcript = join_parts(pieces)

    # Recorded with no cost: Deepgram bills per minute and its rate is not in
    # `usage/pricing.py`, which covers LLM tokens only. Inventing a figure would be worse
    # than none — but omitting the *event* would make a library total that silently
    # excluded transcription read as complete spend, so the seconds are stored and the
    # readout says they are not costed.
    if not suite.enabled():
        # On the suite it has already written this call's seconds, under GAM and this
        # asset. A second row here would count the transcription twice.
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

    return store_transcript(session, asset, transcript)


def store_transcript(session: Session, asset: Asset, transcript: deepgram.Transcript) -> int:
    """Write a transcript, index it, and mark the asset transcribed.

    Split out of `run` because Deepgram is no longer the only source: a URL import with
    no Deepgram key stores the platform's captions through here (`ingest/captions.py`).
    One writer means captions get the same edited-segment protection, the same keyword
    indexing and the same header fields — and a later Deepgram run replaces them exactly
    the way it would replace an earlier Deepgram transcript.
    """
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
