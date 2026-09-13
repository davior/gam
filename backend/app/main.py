"""Application entry point.

Auth is a dependency (`app.auth.CurrentUser`), not a middleware, so there is no
public-path allowlist here to keep in step with the routers — a route is protected
exactly when it declares that it is.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.auth import CurrentUser
from app.config import settings
from app.media_tools import ffmpeg_available, ffmpeg_version
from fastapi import Depends
from sqlmodel import Session

from app.database import get_session
from app.routers import activity as activity_router
from app.routers import assets as assets_router
from app.routers import media as media_router
from app.routers import search as search_router
from app.routers import settings as settings_router
from app.routers import tags as tags_router
from app.routers import transcripts as transcripts_router
from app.schemas import DataResponse, HealthResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VERSION = "0.1.0"

@asynccontextmanager
async def lifespan(_app: FastAPI):
    os.makedirs(settings.resolved_media_dir, exist_ok=True)
    if ffmpeg_available():
        logger.info("ffmpeg available: %s", ffmpeg_version())
    else:
        logger.warning(
            "ffmpeg/ffprobe not found on PATH. Media probing, thumbnails and clip "
            "extraction will be unavailable."
        )
    # Picks up anything a restart interrupted, and starts the worker threads.
    from app.jobs import enrichment

    enrichment.start()

    yield


app = FastAPI(title="Gecko Asset Manager", version=VERSION, lifespan=lifespan)

# Behind Caddy/nginx, so X-Forwarded-* decides scheme and client address. Matches the
# gecko-notes deployment, where the app container is never reachable directly.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

if not settings.cors_origins:
    logger.warning("CORS_ORIGIN is not set — cross-origin API requests will be blocked.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request, exc: StarletteHTTPException):
    """Normalise every error to `{"detail": {"code", "message"}}`.

    FastAPI's own handler passes `detail` straight through, so a raise with a plain
    string would produce a different shape from one raised with a dict. Routers should
    not have to remember which; this makes both come out the same.
    """
    detail = exc.detail
    if not isinstance(detail, dict) or "code" not in detail:
        detail = {"code": _default_code(exc.status_code), "message": str(detail)}
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


def _default_code(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "too_large",
        422: "unprocessable",
    }.get(status_code, "error")


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Unauthenticated on purpose — the container healthcheck calls it."""
    return HealthResponse(status="ok", version=VERSION, ffmpeg=ffmpeg_available())


@app.get("/api/me", response_model=DataResponse[dict])
def me(
    user: CurrentUser, session: Session = Depends(get_session)
) -> DataResponse[dict]:
    """Who is signed in, and the record that they were here.

    The shadow row is written here rather than in the auth dependency because this is
    the one call the frontend makes exactly once per session. Upserting on every
    authenticated request would put a SELECT — and periodically a write — in front of
    every upload and every media byte, for a row nothing on those paths reads.
    """
    from app.services.users import ensure_user

    record = ensure_user(session, user)
    return DataResponse(
        data={
            "id": record.id,
            "username": record.username or user.username,
            "is_admin": record.is_admin,
            "first_seen": record.created_at.isoformat() if record.created_at else None,
        }
    )


app.include_router(assets_router.router, prefix="/api/assets", tags=["assets"])
# Mounted under the same prefix: a transcript belongs to an asset, and the URL should
# say so rather than inventing a parallel /api/transcripts tree.
app.include_router(transcripts_router.router, prefix="/api/assets", tags=["transcripts"])
app.include_router(activity_router.router, prefix="/api/activity", tags=["activity"])
app.include_router(search_router.router, prefix="/api/search", tags=["search"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
app.include_router(tags_router.router, prefix="/api/tags", tags=["tags"])
# Not under /api: these URLs go straight into <img src> and <video src>, and the
# signature in the query string is what authorises them.
app.include_router(media_router.router, prefix="/media", tags=["media"])
