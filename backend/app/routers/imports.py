"""Importing media from a URL — YouTube and the other sites yt-dlp knows.

Its own router rather than another endpoint in `assets.py`, mirroring how clips and
enrichment each got one: the upload path takes bytes, this takes a promise of bytes, and
nothing about the two requests is shared. Mounted under `/api/assets` all the same,
because what it makes is an asset.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.enrichment.import_url import ImportRequest
from app.ingest.ytdlp import MAX_URL, normalise_url
from app.jobs import enrichment as enrichment_jobs
from app.jobs.registry import KINDS
from app.models.job import KIND_IMPORT_URL
from app.schemas import DataResponse
from app.schemas_jobs import ActivityJobRead

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
