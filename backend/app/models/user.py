import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.clock import utcnow


def new_id() -> str:
    return str(uuid.uuid4())


class User(SQLModel, table=True):
    """A shadow of a gecko-notes account.

    GAM does not own identity — there is no password hash here and no registration
    endpoint. Notes signs a token, GAM verifies it, and this row is upserted from the
    token's claims the first time that subject is seen, so assets have something local
    to hang ownership off.

    `id` is therefore not generated here: it is the `sub` claim, and must stay equal to
    the id Notes assigned, or the same person becomes two users.
    """

    id: str = Field(primary_key=True)
    username: str = Field(default="", index=True)
    # Filled in from Notes' /api/auth/session when something needs it. Not required:
    # the token alone does not carry an address.
    email: Optional[str] = None
    is_admin: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
    last_seen: Optional[datetime] = None
