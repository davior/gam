"""Every environment-derived setting, resolved once.

gecko-notes reads `os.getenv` at module scope in a dozen places and resolves
`MEDIA_DIR` independently in four of them, which is why its tests have to
monkeypatch module globals to redirect a path — the value is bound at import time, so
whichever module imported it first wins. One settings object avoids that: tests
override the object, and there is exactly one answer to "where does media live".

Instantiated once as `settings` at the bottom. Import that, not the class.
"""

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# The weak default gecko-notes ships, refused there and refused here. Sharing the
# constant means a copied .env carrying it fails in both apps rather than silently
# signing tokens with a published secret in one of them.
_WEAK_SECRETS = frozenset({
    "",
    "change-me",
    "gecko-notes-secret-change-in-production",
    "gam-secret-change-in-production",
})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ─── identity ────────────────────────────────────────────────────────────
    # Must match gecko-notes'. GAM does not issue tokens — it verifies the ones
    # Notes issued, and a shared HS256 secret is what makes that possible. It also
    # derives the Fernet key that encrypts stored provider API keys, so rotating it
    # invalidates both every session and every stored key.
    #
    # validate_default is load-bearing: pydantic v2 skips validators on defaults, so
    # without it an unset secret would sail past _reject_weak_secret and the app would
    # start up unable to verify anything.
    jwt_secret_key: str = Field(default="", validate_default=True)
    jwt_algorithm: str = "HS256"
    # The cookie gecko-notes sets on the parent domain once GN-1 is applied. Read
    # only; GAM never sets it.
    auth_cookie_name: str = "gecko_session"
    # Where an unauthenticated visitor is sent to sign in.
    notes_base_url: str = "https://notes.geckopico.com"

    # ─── storage ─────────────────────────────────────────────────────────────
    database_url: str = ""
    media_dir: str = ""
    # How long a signed media URL stays valid. Long enough to start playing a large
    # video, short enough that a leaked URL stops working.
    media_url_ttl_seconds: int = 3600

    # ─── http ────────────────────────────────────────────────────────────────
    cors_origin: str = ""

    # ─── jobs ────────────────────────────────────────────────────────────────
    job_heartbeat_seconds: int = 30
    job_stale_minutes: int = 40
    # One at a time by default. Transcription is an ffmpeg transcode followed by a
    # long upload, both of which contend with serving the API from the same container.
    enrichment_concurrency: int = 1

    # ─── import from URL ─────────────────────────────────────────────────────
    # The tallest video an import will fetch. 1080p is where YouTube stops offering
    # H.264, which is the codec every browser (Safari included) plays without a
    # re-encode — and a ninety-minute interview is ~1-2 GB here against 4-8 GB at 4K.
    url_import_max_height: int = 1080
    # A channel's "videos" tab is a playlist too, and can hold thousands. One paste
    # should not be able to queue a week of downloads.
    url_import_max_playlist_items: int = 200
    # A Netscape-format cookies.txt, as a path inside the container. Empty by default.
    # For YouTube's "confirm you're not a bot" wall on datacenter IPs, and for
    # age-restricted or members-only videos; mount the file read-only and point here.
    url_import_cookies_file: str = ""

    # ─── administration ──────────────────────────────────────────────────────
    # Comma-separated user ids or usernames. The Notes token carries no admin claim and
    # nothing in GAM can grant one, so this is the only way anybody becomes an admin —
    # which today means being allowed to edit the generation model catalogue. Both match
    # exactly: Notes usernames are unique only case-sensitively, and anyone can rename.
    # Ids are the safer entry — a name freed by a rename can be taken by someone else.
    admin_users: str = ""

    # ─── AI generation (M8) ──────────────────────────────────────────────────
    # Settings, not constants, so a test can point both at a stub and an operator can
    # follow fal if it moves a host. Each user's own key is what authenticates.
    fal_queue_base_url: str = "https://queue.fal.run"
    # The platform API, used here only for unit prices.
    fal_api_base_url: str = "https://api.fal.ai"
    # Its own worker pool, apart from `enrichment_concurrency`: a generation is almost
    # entirely waiting on fal, and a four-minute video on the shared queue would hold
    # every transcription behind it.
    generation_concurrency: int = 2
    # GAM's own deadline on a fal request. fal reports no terminal "failed" status a
    # client can rely on, so without one an unknown status would be waited on forever.
    # Past it the request is cancelled at fal — a remote job left running keeps billing.
    generation_timeout_minutes: int = 30
    generation_poll_seconds: float = 3
    # Per output, enforced while streaming rather than after: a video arrives as one
    # file and a size checked afterwards has already filled the disk.
    generation_max_download_mb: int = 1024
    # Base images travel inside the request as data URIs, so their size is request
    # size. 2048 px on the long edge is more than any seeded model reads.
    generation_base_max_edge: int = 2048
    generation_max_data_uri_mb: int = 8

    # ─── development ─────────────────────────────────────────────────────────
    # Bypasses token verification and acts as this user id. Refused unless
    # `environment` is "development", so it cannot be switched on in production by
    # setting one variable.
    environment: str = "production"
    dev_auth_user: str = ""

    @field_validator("jwt_secret_key")
    @classmethod
    def _reject_weak_secret(cls, value: str) -> str:
        if value.strip() in _WEAK_SECRETS:
            raise ValueError(
                "JWT_SECRET_KEY must be set to a strong secret before starting. "
                "It must match the value gecko-notes uses, since GAM verifies the "
                "tokens Notes issues. Generate one with: openssl rand -hex 32"
            )
        return value

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{REPO_ROOT / 'data' / 'db' / 'gam.db'}"

    @property
    def resolved_media_dir(self) -> Path:
        if self.media_dir:
            return Path(self.media_dir)
        return REPO_ROOT / "data" / "media"

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.cors_origin.split(",") if o.strip()]

    @property
    def admin_user_list(self) -> List[str]:
        return [u.strip() for u in self.admin_users.split(",") if u.strip()]

    @property
    def is_development(self) -> bool:
        return self.environment.strip().lower() in {"development", "dev", "local"}

    @property
    def dev_auth_enabled(self) -> bool:
        """Whether to accept requests with no token at all.

        Two conditions, not one. `DEV_AUTH_USER` alone is not enough: a production
        .env that picked the variable up from a dev template would otherwise turn
        authentication off entirely.
        """
        return bool(self.dev_auth_user) and self.is_development


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
