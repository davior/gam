from sqlmodel import Field, SQLModel


class UserSetting(SQLModel, table=True):
    """One per-user preference or credential, keyed by name.

    A key-value table rather than columns on the user, because what is configurable
    grows every milestone (Deepgram now; fal.ai, an LLM provider and an embedding
    model later) and each addition would otherwise be a migration.

    Values are JSON-encoded text. Anything secret is additionally Fernet-encrypted
    before it is encoded — see app.settings_store.
    """

    user_id: str = Field(primary_key=True)
    key: str = Field(primary_key=True)
    value: str = Field(default="")
