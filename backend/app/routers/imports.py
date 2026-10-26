"""Importing media from a URL — YouTube and the other sites yt-dlp knows.

Its own router rather than another endpoint in `assets.py`, mirroring how clips and
enrichment each got one: the upload path takes bytes, this takes a promise of bytes, and
nothing about the two requests is shared. Mounted under `/api/assets` all the same,
because what it makes is an asset.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.enrichment import page_import
from app.enrichment.import_url import ImportRequest
from app.ingest.page_tracks import PageTrack
from app.ingest.ytdlp import MAX_URL, normalise_url
from app.jobs import enrichment as enrichment_jobs
from app.jobs.registry import KINDS
from app.models.job import KIND_IMPORT_TRACK, KIND_IMPORT_URL
from app.schemas import DataResponse
from app.safe_url import require_safe_external_url
from app.schemas_jobs import ActivityJobRead
from app.storage import capacity

router = APIRouter()


class UrlImportCreate(BaseModel):
    url: str = Field(min_length=1, max_length=MAX_URL)
    # Just the soundtrack, as .m4a — a talk or a podcast where the picture is a
    # static frame, at a tenth of the size.
    audio_only: bool = False
    # On by default at the user's request; off routes the uploader's tags through the
    # suggestion queue instead. See `enrichment/import_url.py::_apply_tags`.
    apply_tags: bool = True
    chapters_as_clips: bool = True


@router.post(
    "/import", response_model=DataResponse[ActivityJobRead], status_code=status.HTTP_202_ACCEPTED
)
def start_url_import(
    payload: UrlImportCreate,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[ActivityJobRead]:
    """Queue an import. Returns the job, which the activity feed then follows.

    Nothing here touches the network beyond the SSRF guard's DNS lookup. Reading a page
    with yt-dlp takes seconds — longer when YouTube wants a JavaScript challenge solved —
    and a request that did it inline would hold the browser for all of it, then do the
    work again in the job. Whether the link is a video, a playlist, or nothing at all is
    the job's first step, and its answer arrives the way every other job's does.

    No duplicate check either, for the same reason: `youtu.be/x` and
    `youtube.com/watch?v=x` are the same video, and only the site can say so. The job
    finds an existing copy once it knows the canonical address, and finishes pointing at
    it rather than downloading it twice.
    """
    url = normalise_url(payload.url)
    capacity.require_room()
    request = ImportRequest(
        url=url,
        audio_only=payload.audio_only,
        apply_tags=payload.apply_tags,
        chapters_as_clips=payload.chapters_as_clips,
    )
    job = enrichment_jobs.submit_library(
        session, user.id, KIND_IMPORT_URL, payload=request.encode(), asset_name=url[:255]
    )
    return DataResponse(data=KINDS["enrichment"].to_activity(job))


# ─── tracks from a music library's page ──────────────────────────────────────


class PageTrackBody(BaseModel):
    """One track, as the preview found it and the person corrected it."""

    audio_url: str = Field(min_length=1, max_length=MAX_URL)
    title: str = Field(min_length=1, max_length=255)
    creator: Optional[str] = Field(default=None, max_length=500)
    publisher: Optional[str] = Field(default=None, max_length=500)
    source_title: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=20_000)
    duration_seconds: Optional[float] = None
    published_date: Optional[str] = Field(default=None, max_length=10)
    license: Optional[str] = Field(default=None, max_length=500)
    credit_line: Optional[str] = Field(default=None, max_length=2_000)
    source_url: Optional[str] = Field(default=None, max_length=MAX_URL)
    genres: List[str] = Field(default_factory=list, max_length=20)
    moods: List[str] = Field(default_factory=list, max_length=20)
    instruments: List[str] = Field(default_factory=list, max_length=20)
    tags: List[str] = Field(default_factory=list, max_length=50)
    bpm: Optional[float] = None


class PageTrackRead(PageTrackBody):
    # The asset this library already holds for the track, so the review list can show it
    # as imported rather than offering it twice.
    existing_asset_id: Optional[str] = None


class PagePreviewRequest(BaseModel):
    url: str = Field(min_length=1, max_length=MAX_URL)
    # Off skips the AI fallback, which costs a little per page.
    use_ai: bool = True


class PagePreview(BaseModel):
    page_url: str
    # structured | ai | links: how much the details can be trusted.
    method: str
    source: str
    note: str = ""
    tracks: List[PageTrackRead]


class PageImportRequest(BaseModel):
    tracks: List[PageTrackBody] = Field(min_length=1, max_length=page_import.MAX_TRACKS_PER_IMPORT)


def _page_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "page_unreadable", "message": str(exc)},
    )


@router.post("/import-page/preview", response_model=DataResponse[PagePreview])
def preview_page_import(
    payload: PagePreviewRequest,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[PagePreview]:
    """Read a page and list the tracks on it, for the person to choose from.

    Synchronous, unlike a URL import: the answer is a list to review, not work to watch,
    and reading a page (and, without structured data, asking the AI about it) takes
    seconds rather than minutes. Nothing is downloaded here.
    """
    url = normalise_url(payload.url)
    try:
        extraction = page_import.preview(session, user.id, url, use_ai=payload.use_ai)
    except page_import.PageImportError as exc:
        raise _page_error(exc) from exc

    existing = page_import.already_imported(session, user.id, extraction.tracks)
    return DataResponse(
        data=PagePreview(
            page_url=extraction.page_url,
            method=extraction.method,
            source=extraction.source,
            note=extraction.note,
            tracks=[
                PageTrackRead(**t.to_dict(), existing_asset_id=existing.get(t.audio_url))
                for t in extraction.tracks
            ],
        )
    )


@router.post(
    "/import-page",
    response_model=DataResponse[List[ActivityJobRead]],
    status_code=status.HTTP_202_ACCEPTED,
)
def start_page_import(
    payload: PageImportRequest,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[List[ActivityJobRead]]:
    """Queue one download per chosen track.

    The tracks come back from the browser, so every audio URL is checked again here: the
    review list is editable, and a URL in it is the user's, not the page's.
    """
    capacity.require_room()
    for track in payload.tracks:
        require_safe_external_url(track.audio_url)

    jobs = []
    for body in payload.tracks:
        track = PageTrack(**body.model_dump())
        job = enrichment_jobs.submit_library(
            session,
            user.id,
            KIND_IMPORT_TRACK,
            payload=page_import.encode_track(track),
            asset_name=track.title,
        )
        jobs.append(KINDS["enrichment"].to_activity(job))
    return DataResponse(data=jobs)
