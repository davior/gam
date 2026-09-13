"""Starting a transcription, and reading or correcting the result."""

from __future__ import annotations

import json
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from app.auth import CurrentUser
from app.clock import utcnow
from app.database import get_session
from app.enrichment.transcribe import can_transcribe
from app.jobs import enrichment as enrichment_jobs
from app.jobs.runner import ACTIVE_STATUSES
from app.models.asset import Asset
from app.models.job import EnrichmentJob, KIND_TRANSCRIBE
from app.models.transcript import TranscriptSegment
from app.schemas import DataResponse
from app.schemas_jobs import ActivityJobRead
from app.jobs.registry import KINDS

router = APIRouter()


class WordRead(BaseModel):
    text: str
    start_time: float
    end_time: float


class SegmentRead(BaseModel):
    id: str
    idx: int
    text: str
    start_time: float
    end_time: float
    speaker: Optional[int] = None
    edited: bool = False
    words: List[WordRead] = Field(default_factory=list)


class TranscriptRead(BaseModel):
    """The nested shape the requirements describe, assembled from the segment rows."""

    asset_id: str
    status: Optional[str] = None
    model: Optional[str] = None
    language: Optional[str] = None
    segments: List[SegmentRead] = Field(default_factory=list)


class SegmentUpdate(BaseModel):
    text: Optional[str] = Field(default=None, max_length=10_000)
    start_time: Optional[float] = Field(default=None, ge=0)
    end_time: Optional[float] = Field(default=None, ge=0)


def _owned_asset(asset_id: str, user_id: str, session: Session) -> Asset:
    asset = session.get(Asset, asset_id)
    if not asset or asset.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "No such asset"},
        )
    return asset


def _words_of(segment: TranscriptSegment) -> List[WordRead]:
    try:
        raw: Any = json.loads(segment.words or "[]")
    except ValueError:
        return []
    if not isinstance(raw, list):
        return []
    return [
        WordRead(text=str(w.get("w", "")), start_time=float(w.get("s", 0)), end_time=float(w.get("e", 0)))
        for w in raw
        if isinstance(w, dict)
    ]


@router.post("/{asset_id}/transcribe", response_model=DataResponse[ActivityJobRead], status_code=202)
def start_transcription(
    asset_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[ActivityJobRead]:
    """Queue a transcription and return immediately.

    202 rather than 201: the transcript does not exist yet, and what is being reported
    is that the work was accepted.
    """
    asset = _owned_asset(asset_id, user.id, session)

    if not can_transcribe(asset):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "not_transcribable",
                "message": "Only audio and video assets can be transcribed",
            },
        )

    # One at a time per asset. Two concurrent runs would race to replace the same
    # segments and bill twice for the same audio.
    existing = session.exec(
        select(EnrichmentJob)
        .where(
            EnrichmentJob.asset_id == asset.id,
            EnrichmentJob.kind == KIND_TRANSCRIBE,
            col(EnrichmentJob.status).in_(ACTIVE_STATUSES),
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_running",
                "message": "This asset is already being transcribed",
            },
        )

    job = EnrichmentJob(
        user_id=user.id,
        asset_id=asset.id,
        kind=KIND_TRANSCRIBE,
        status="queued",
        stage="Queued",
        asset_name=asset.name,
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    enrichment_jobs.enqueue(job.id)

    return DataResponse(data=KINDS["enrichment"].to_activity(job))


@router.get("/{asset_id}/transcript", response_model=DataResponse[TranscriptRead])
def read_transcript(
    asset_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[TranscriptRead]:
    asset = _owned_asset(asset_id, user.id, session)

    segments = session.exec(
        select(TranscriptSegment)
        .where(TranscriptSegment.asset_id == asset.id)
        .order_by(col(TranscriptSegment.idx))
    ).all()

    return DataResponse(
        data=TranscriptRead(
            asset_id=asset.id,
            status=asset.transcript_status,
            model=asset.transcript_model,
            language=asset.transcript_language,
            segments=[
                SegmentRead(
                    id=s.id,
                    idx=s.idx,
                    text=s.text,
                    start_time=s.start_time,
                    end_time=s.end_time,
                    speaker=s.speaker,
                    edited=s.edited,
                    words=_words_of(s),
                )
                for s in segments
            ],
        )
    )


@router.patch("/{asset_id}/transcript/{segment_id}", response_model=DataResponse[SegmentRead])
def update_segment(
    asset_id: str,
    segment_id: str,
    payload: SegmentUpdate,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[SegmentRead]:
    """Correct one segment by hand (FR 7.1.3).

    Marks it `edited`, which is what stops a later re-run overwriting the correction.
    """
    _owned_asset(asset_id, user.id, session)

    segment = session.get(TranscriptSegment, segment_id)
    if not segment or segment.asset_id != asset_id or segment.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "No such transcript segment"},
        )

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return DataResponse(data=_segment_read(segment))

    if "text" in changes and changes["text"] is not None:
        segment.text = changes["text"]
        # The stored word timings belong to the machine's wording. Once a person
        # rewrites the text they no longer describe it, and keeping them would have
        # search highlight the wrong words.
        segment.words = "[]"
    if changes.get("start_time") is not None:
        segment.start_time = changes["start_time"]
    if changes.get("end_time") is not None:
        segment.end_time = changes["end_time"]

    if segment.end_time and segment.start_time > segment.end_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "bad_request", "message": "A segment cannot end before it starts"},
        )

    segment.edited = True
    session.add(segment)

    asset = session.get(Asset, asset_id)
    if asset:
        asset.metadata_modified_date = utcnow()
        session.add(asset)

    session.commit()
    session.refresh(segment)

    return DataResponse(data=_segment_read(segment))


def _segment_read(segment: TranscriptSegment) -> SegmentRead:
    return SegmentRead(
        id=segment.id,
        idx=segment.idx,
        text=segment.text,
        start_time=segment.start_time,
        end_time=segment.end_time,
        speaker=segment.speaker,
        edited=segment.edited,
        words=_words_of(segment),
    )
