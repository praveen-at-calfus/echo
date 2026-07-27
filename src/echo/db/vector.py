"""The ``embeddings`` table + pgvector extension + HNSW cosine index.

Plain English: this is where each feedback item's semantic "fingerprint" lives —
a 1536-number vector that lets us find items that *mean* the same thing even when
they share no words ("app crashes on login" ≈ "sign-in keeps failing"). Themes
(clustering) and the "ask echo" Q&A (retrieval) both read from here.

Technically: the table lives on the shared :data:`echo.db.schema.metadata` (so its
FK to ``feedback`` resolves and the repo keeps its single-metadata convention),
but it needs the pgvector extension present *before* its DDL runs — the ``vector``
column type and the HNSW index don't exist otherwise. So :func:`create_all` calls
:func:`ensure_extension` first and creates only this table. Other stages never
import this module, so they never emit vector DDL on an extension-less DB.
"""

from __future__ import annotations

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    func,
    text,
)

from echo import config
from echo.db import schema

# One vector per feedback item, tagged with the model that produced it (so a model
# swap is a new set of rows, not a silent mix). PK = item_id → idempotent re-runs.
embeddings = Table(
    "embeddings",
    schema.metadata,
    Column("item_id", String, ForeignKey("feedback.item_id", ondelete="CASCADE"), primary_key=True),
    Column("model", String, nullable=False),
    Column("embedding", Vector(config.EMBED_DIM), nullable=False),
    Column("created_at", DateTime, server_default=func.now()),
)

# Approximate-nearest-neighbour index for fast cosine top-k (themes + RAG).
Index(
    "ix_embeddings_hnsw",
    embeddings.c.embedding,
    postgresql_using="hnsw",
    postgresql_with=config.EMBED_HNSW,
    postgresql_ops={"embedding": "vector_cosine_ops"},
)


def ensure_extension(engine) -> None:
    """Enable pgvector (idempotent). Must run before any vector DDL/queries."""
    with engine.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


def create_all(engine) -> None:
    """Enable the extension, then create the embeddings table + HNSW index."""
    ensure_extension(engine)
    schema.metadata.create_all(engine, tables=[embeddings], checkfirst=True)
