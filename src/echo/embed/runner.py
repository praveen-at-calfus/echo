"""Batch embedding engine.

Plain English: turn each feedback item's text into a 1536-number semantic vector
(via OpenAI ``text-embedding-3-small``) and store it in Postgres, so later stages
can find items that mean the same thing. Identical texts are embedded once and
reused; items already embedded are skipped — so re-runs are free.

Technically: mirrors the classify runner's idempotent-query + content-dedup +
SQLAlchemy Core bulk-insert patterns. The DB itself is the durable cache (PK on
item_id, ON CONFLICT DO NOTHING); within a run, unique normalized texts are
embedded once and fanned back out to every item that shares them.
"""

from __future__ import annotations

import time

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tenacity import retry, stop_after_attempt, wait_exponential

from echo import config
from echo.corpus.utils import normalize_text
from echo.db import schema, vector

_PRICE = 0.02  # text-embedding-3-small, USD per 1M tokens


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=30), reraise=True)
def _embed_batch(client, texts: list[str]) -> tuple[list[list[float]], int]:
    """Call OpenAI once to turn a batch of texts into vectors (retrying automatically on transient failures); returns the vectors and the total tokens billed."""
    resp = client.embeddings.create(model=config.EMBED_MODEL, input=texts, dimensions=config.EMBED_DIM)
    return [d.embedding for d in resp.data], int(resp.usage.total_tokens)


def run(limit: int | None = None) -> dict:
    """Embed every feedback item that doesn't have a vector yet (optionally capped at `limit` items), store the vectors in Postgres, and return a summary dict of what was embedded and its cost."""
    if config.settings.use_offline:
        raise SystemExit("embed needs OPENAI_API_KEY (set it in .env).")
    model = config.EMBED_MODEL
    engine = create_engine(config.settings.database_url)
    vector.create_all(engine)  # ensure_extension + embeddings table + HNSW index
    e, f = vector.embeddings, schema.feedback

    # 1) texted items with no embedding yet for this model (idempotent)
    j = f.outerjoin(e, (e.c.item_id == f.c.item_id) & (e.c.model == model))
    q = (select(f.c.item_id, f.c.text).select_from(j)
         .where((func.length(func.trim(f.c.text)) > 0) & (e.c.item_id.is_(None)))
         .order_by(f.c.item_id))
    if limit:
        q = q.limit(limit)
    with engine.connect() as c:
        items = [dict(r._mapping) for r in c.execute(q)]
    if not items:
        print("embed: nothing to do (all texted items already embedded for this model).")
        return {"embedded": 0}

    # 2) dedup identical texts -> embed each unique text once
    uniq: dict[str, str] = {}          # normalized -> a representative raw text
    item_norm: dict[str, str] = {}     # item_id -> normalized key
    for it in items:
        key = normalize_text(it["text"])
        item_norm[it["item_id"]] = key
        uniq.setdefault(key, it["text"])
    keys = list(uniq)
    print(f"embed: {len(items)} items · {len(keys)} unique texts · model={model} · dim={config.EMBED_DIM}")

    # 3) embed unique texts in batches
    from openai import OpenAI
    client = OpenAI(api_key=config.settings.openai_api_key)
    vec_by_key: dict[str, list[float]] = {}
    tot_tokens = 0
    t0 = time.perf_counter()
    for i in range(0, len(keys), config.EMBED_BATCH):
        chunk = keys[i:i + config.EMBED_BATCH]
        vecs, toks = _embed_batch(client, [uniq[k] for k in chunk])
        vec_by_key.update(zip(chunk, vecs))
        tot_tokens += toks
        print(f"  batch {i // config.EMBED_BATCH + 1}/{-(-len(keys) // config.EMBED_BATCH)} "
              f"· {i + len(chunk)}/{len(keys)} texts", end="\r")
    print()

    # 4) bulk insert one row per item (ON CONFLICT DO NOTHING)
    rows = [{"item_id": iid, "model": model, "embedding": vec_by_key[k]} for iid, k in item_norm.items()]
    with engine.begin() as c:
        stmt = pg_insert(e).on_conflict_do_nothing(index_elements=[e.c.item_id])
        for i in range(0, len(rows), 1000):
            c.execute(stmt, rows[i:i + 1000])

    cost = tot_tokens / 1e6 * _PRICE
    with engine.connect() as c:
        total = c.execute(select(func.count()).select_from(e).where(e.c.model == model)).scalar()
    print(f"  embedded {len(rows)} items ({len(keys)} unique) in {time.perf_counter() - t0:.1f}s · "
          f"{tot_tokens:,} tokens → est. ${cost:.4f}")
    print(f"  embeddings table now holds {total:,} rows for {model}")
    return {"embedded": len(rows), "unique": len(keys), "tokens": tot_tokens,
            "est_cost": round(cost, 4), "total_rows": int(total)}


_CLIENT = None


def _client():
    """Lazily create and cache a single OpenAI client, reused by every call to embed_texts()."""
    global _CLIENT
    if _CLIENT is None:
        from openai import OpenAI
        _CLIENT = OpenAI(api_key=config.settings.openai_api_key)
    return _CLIENT


def embed_texts(texts: list[str]) -> tuple[list[list[float]], int]:
    """Embed a small batch live (API path). Returns (vectors, total_tokens)."""
    return _embed_batch(_client(), list(texts))


def nearest(engine, item_id: str, k: int = 5, model: str | None = None) -> list[dict]:
    """Cosine top-k neighbours of an item (excludes itself). For reuse + verification."""
    model = model or config.EMBED_MODEL
    sql = text("""
        SELECT n.item_id, f.source_type, left(f.text, 90) AS snippet,
               q.embedding <=> n.embedding AS distance
        FROM embeddings q
        JOIN embeddings n ON n.model = q.model AND n.item_id <> q.item_id
        JOIN feedback f ON f.item_id = n.item_id
        WHERE q.item_id = :iid AND q.model = :model
        ORDER BY q.embedding <=> n.embedding
        LIMIT :k
    """)
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(sql, {"iid": item_id, "model": model, "k": k})]
