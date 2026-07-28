"""pgvector cosine top-k retrieval for a live query vector.

Plain English: given a customer question turned into a vector (a list of
numbers capturing its meaning), find the K feedback items whose vectors are
closest to it — same cosine-distance search that powers themes, just against
a question instead of another item.

Technically: unlike ``embed.nearest`` (which compares two stored columns), here
the query side is a Python vector with no row of its own, so it's passed as a
bind parameter formatted as a pgvector text literal (``"[v1,v2,...]"``) and cast
with ``CAST(:qvec AS vector)`` — Postgres's vector input parser accepts that
format directly, no driver-level vector registration required.
"""

from __future__ import annotations

from sqlalchemy import text

from echo import config


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def top_k(engine, query_vector: list[float], k: int, embed_model: str | None = None,
          analysis_model: str | None = None, analysis_pv: str | None = None) -> list[dict]:
    """Cosine-nearest ``k`` feedback items to ``query_vector``, with their classification.

    Each result carries item_id, source_type, text, order_value, created_at,
    category/sentiment/urgency (from the current analysis version, may be null
    for score-only items with no LLM classification), and the cosine distance.
    """
    embed_model = embed_model or config.EMBED_MODEL
    analysis_model = analysis_model or config.settings.model
    analysis_pv = analysis_pv or config.CLASSIFY_PROMPT_VERSION
    qvec = _vector_literal(query_vector)

    sql = text("""
        SELECT e.item_id, f.source_type, f.text, f.order_value, f.created_at::text AS created_at,
               a.category, a.sentiment, a.urgency,
               e.embedding <=> CAST(:qvec AS vector) AS distance
        FROM embeddings e
        JOIN feedback f ON f.item_id = e.item_id
        LEFT JOIN analysis a ON a.item_id = e.item_id
                             AND a.model_name = :amodel AND a.prompt_version = :apv
        WHERE e.model = :emodel
        ORDER BY e.embedding <=> CAST(:qvec AS vector)
        LIMIT :k
    """)
    with engine.connect() as c:
        rows = [dict(r._mapping) for r in c.execute(
            sql, {"qvec": qvec, "emodel": embed_model, "amodel": analysis_model,
                  "apv": analysis_pv, "k": k})]
    for r in rows:
        r["order_value"] = float(r["order_value"]) if r["order_value"] is not None else None
        r["distance"] = float(r["distance"])
    return rows
