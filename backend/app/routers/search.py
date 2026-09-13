"""Search."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.ingest.filetypes import ASSET_TYPES
from app.routers.assets import get_storage
from app.schemas_assets import AssetRead
from app.services import assets as asset_service
from app.services import search as search_service
from app.storage import LocalStorage

router = APIRouter()


class SearchHit(BaseModel):
    asset: AssetRead
    score: float
    # The excerpt that matched, with «guillemets» around the matched words when the hit
    # came from keyword search.
    snippet: str = ""
    # Seconds into the asset. Null for a metadata hit, which is about the file rather
    # than a moment in it.
    start_time: Optional[float] = None
    segment_id: Optional[str] = None
    # Which retrievers found it — "keyword", "semantic", or both.
    sources: List[str] = Field(default_factory=list)
    other_matches: int = 0


class SearchResponse(BaseModel):
    data: List[SearchHit]
    total: int
    query: str
    # False when no embedding provider is configured, or when one failed. "No results"
    # means something different if only half the search ran, and the UI says so.
    semantic: bool = False
    semantic_error: Optional[str] = None


@router.get("", response_model=SearchResponse)
def search(
    user: CurrentUser,
    q: str = Query(default="", max_length=500),
    asset_type: Optional[str] = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> SearchResponse:
    if asset_type and asset_type not in ASSET_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "bad_request", "message": f"Unknown asset type: {asset_type}"},
        )

    outcome = search_service.search(session, user.id, q, limit=limit, asset_type=asset_type)
    assets = search_service.load_assets(
        session, user.id, [a.asset_id for a in outcome.assets]
    )

    hits = [
        SearchHit(
            asset=asset_service.to_read_model(assets[fused.asset_id], storage),
            score=round(fused.score, 6),
            snippet=fused.snippet,
            start_time=fused.start_time,
            segment_id=fused.segment_id,
            sources=sorted(fused.sources),
            other_matches=fused.other_matches,
        )
        # An asset can vanish between retrieval and load; skip rather than 500.
        for fused in outcome.assets
        if fused.asset_id in assets
    ]

    return SearchResponse(
        data=hits,
        total=len(hits),
        query=q.strip(),
        semantic=outcome.semantic_ran,
        semantic_error=outcome.semantic_error,
    )
