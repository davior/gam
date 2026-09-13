"""Reading and writing per-user settings, including the secret ones.

gecko-notes has five near-identical `load_*_api_key` functions — one per provider,
each about fifteen lines of the same select, json.loads and decrypt, each with its own
bare except. This is that, once.

A secret is Fernet-encrypted before it is stored and is never sent to the browser: the
API reports only whether a key is present, and the app makes the upstream calls itself.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlmodel import Session, select

from app.auth import decrypt_api_key, encrypt_api_key
from app.models.setting import UserSetting

logger = logging.getLogger(__name__)

# Setting keys. Named here so a typo is an import error rather than a silently absent
# value that reads as "not configured".
DEEPGRAM_API_KEY = "deepgram_api_key"
DEEPGRAM_MODEL = "deepgram_model"

EMBEDDING_PROVIDER = "embedding_provider"
EMBEDDING_MODEL = "embedding_model"
EMBEDDING_DIMENSIONS = "embedding_dimensions"
OPENAI_API_KEY = "openai_api_key"
OLLAMA_BASE_URL = "ollama_base_url"

# Keys whose value is encrypted at rest. Anything not in here is stored as plain JSON.
SECRET_KEYS = frozenset({DEEPGRAM_API_KEY, OPENAI_API_KEY})


def get_setting(session: Session, user_id: str, key: str, default: Any = None) -> Any:
    """Read one setting, decrypting it if it is a secret.

    Returns `default` for anything unreadable rather than raising. A settings row that
    cannot be decoded — written by an older version, or encrypted under a secret that
    has since been rotated — should read as "not configured", which is recoverable,
    rather than breaking every request that touches settings.
    """
    row = session.exec(
        select(UserSetting).where(UserSetting.user_id == user_id, UserSetting.key == key)
    ).first()
    if not row or not row.value:
        return default

    try:
        value = json.loads(row.value)
    except ValueError:
        logger.warning("Setting %s for %s is not valid JSON; treating as unset", key, user_id)
        return default

    if key in SECRET_KEYS and isinstance(value, str):
        decrypted = decrypt_api_key(value)
        return decrypted or default

    return value


def set_setting(session: Session, user_id: str, key: str, value: Any) -> None:
    """Write one setting, encrypting it if it is a secret."""
    stored = value
    if key in SECRET_KEYS and isinstance(value, str) and value:
        stored = encrypt_api_key(value)

    row = session.exec(
        select(UserSetting).where(UserSetting.user_id == user_id, UserSetting.key == key)
    ).first()
    if row:
        row.value = json.dumps(stored)
    else:
        row = UserSetting(user_id=user_id, key=key, value=json.dumps(stored))
    session.add(row)
    session.commit()


def clear_setting(session: Session, user_id: str, key: str) -> None:
    row = session.exec(
        select(UserSetting).where(UserSetting.user_id == user_id, UserSetting.key == key)
    ).first()
    if row:
        session.delete(row)
        session.commit()


def has_setting(session: Session, user_id: str, key: str) -> bool:
    """Whether a value is present, without revealing it.

    What the settings API reports for a secret: the browser is told a key is
    configured, never what it is.
    """
    return bool(get_setting(session, user_id, key))


def load_deepgram_key(session: Session, user_id: str) -> Optional[str]:
    return get_setting(session, user_id, DEEPGRAM_API_KEY) or None


def load_openai_key(session: Session, user_id: str) -> Optional[str]:
    return get_setting(session, user_id, OPENAI_API_KEY) or None
