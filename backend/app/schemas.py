"""The response envelope every router returns.

Single resources are `{"data": T}` and lists are `{"data": [...], "total", "limit",
"offset"}`. Errors are `{"detail": {"code", "message"}}` — a machine-readable code so
the frontend can branch, and a sentence so it has something to show when it cannot.

Carried over from gecko-notes deliberately: the two frontends then share an error
handler and a client, and neither has to learn a second shape.
"""

from typing import Generic, List, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class DataResponse(BaseModel, Generic[T]):
    data: T


class ListResponse(BaseModel, Generic[T]):
    data: List[T]
    total: int = 0
    limit: int = 0
    offset: int = 0


class ErrorDetail(BaseModel):
    code: str
    message: str


class MessageResponse(BaseModel):
    message: str


class HealthResponse(BaseModel):
    status: str
    version: str
    # Reported so a deployment can be checked for the media tooling it needs without
    # waiting for the first upload to fail.
    ffmpeg: bool
