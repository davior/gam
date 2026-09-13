import uuid

from sqlalchemy import Index, LargeBinary
from sqlmodel import Column, Field, SQLModel

# What an embedding is attached to. Segments are the interesting case — they are what
# carries a timestamp — but an asset's own description and summary are embedded too, so
# a photo with no speech in it is still findable by meaning.
OWNER_SEGMENT = "segment"
OWNER_ASSET = "asset"


class Embedding(SQLModel, table=True):
    """One vector, stored as raw float32 bytes.

    A BLOB rather than JSON: a 512-dimension vector is 2 KB packed and roughly 10 KB as
    JSON text, and it has to be parsed back into floats on every search. Packed bytes go
    straight into numpy with no decode step, which is what makes brute-force cosine over
    a whole library fast enough to not need a vector database.

    `model` and `dim` are stored per row because they will not always agree: changing
    provider or dimension leaves older vectors behind, and a search has to be able to
    tell which ones it can still compare against rather than silently mixing geometries.
    """

    # Search loads every vector a user owns at once, so that is the access pattern the
    # index serves.
    __table_args__ = (Index("ix_embedding_user_model", "user_id", "model"),)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(index=True)

    owner_kind: str = Field(index=True)  # segment | asset
    owner_id: str = Field(index=True)
    # Denormalised so a hit resolves to its asset without a join back through segments.
    asset_id: str = Field(index=True)

    model: str = Field(default="")
    dim: int = Field(default=0)
    vector: bytes = Field(sa_column=Column(LargeBinary))
