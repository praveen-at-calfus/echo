"""Weekly semantic clustering of feedback embeddings.

Plain English: group a week's actionable feedback by *meaning* (using the vectors
from the embed stage) so recurring issues emerge on their own — "app crashes on
login" and "sign-in keeps failing" land in one cluster despite sharing no words.
No LLM here; this is pure vector math.

Technically: agglomerative clustering with a cosine distance cut
(``CLUSTER_DISTANCE_THRESHOLD``), keeping clusters of at least ``MIN_CLUSTER_SIZE``.
For each kept cluster we also pick the member nearest the centroid as its
representative (the most "typical" item), used later for the quote + labelling.
"""

from __future__ import annotations

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sqlalchemy import and_, func, select

from echo import config
from echo.db import schema, vector
from echo.money.engine import week_bounds


def _fetch(engine, week: str, model: str, pv: str):
    """(item_ids, X) for the week's actionable, embedded items."""
    e, f, a = vector.embeddings, schema.feedback, schema.analysis
    start, end = week_bounds(week)
    q = (select(f.c.item_id, e.c.embedding)
         .select_from(f.join(e, and_(e.c.item_id == f.c.item_id, e.c.model == config.EMBED_MODEL))
                       .join(a, a.c.item_id == f.c.item_id))
         .where(and_(a.c.model_name == model, a.c.prompt_version == pv,
                     a.c.sentiment.in_(config.THEME_SENTIMENTS),
                     f.c.created_at >= start, f.c.created_at < end,
                     func.length(func.trim(f.c.text)) > 0))
         .order_by(f.c.item_id))
    with engine.connect() as c:
        rows = c.execute(q).all()
    if not rows:
        return [], np.empty((0, config.EMBED_DIM))
    ids = [r.item_id for r in rows]
    X = np.asarray([np.asarray(r.embedding, dtype=np.float32) for r in rows])
    return ids, X


def cluster_week(engine, week: str, model: str, pv: str,
                 threshold: float | None = None) -> list[dict]:
    """Return kept clusters: {item_ids, size, representative_item_id}, unranked."""
    ids, X = _fetch(engine, week, model, pv)
    if len(ids) < config.MIN_CLUSTER_SIZE:
        return []
    threshold = config.CLUSTER_DISTANCE_THRESHOLD if threshold is None else threshold

    # group items by how similar their meaning-vectors are (cosine distance), instead
    # of asking for a fixed number of groups upfront: any two items closer than
    # "threshold" can end up in the same group, and groups keep merging until nothing
    # left is close enough
    labels = AgglomerativeClustering(
        n_clusters=None, distance_threshold=threshold,
        metric="cosine", linkage="average",
    ).fit_predict(X)

    # L2-normalize once so a mean vector's nearest member = cosine-nearest to centroid.
    Xn = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)
    clusters = []
    for lab in np.unique(labels):
        idx = np.where(labels == lab)[0]
        if len(idx) < config.MIN_CLUSTER_SIZE:
            continue
        centroid = Xn[idx].mean(axis=0)
        rep_local = idx[int(np.argmax(Xn[idx] @ centroid))]
        clusters.append({
            "item_ids": [ids[i] for i in idx],
            "size": int(len(idx)),
            "representative_item_id": ids[rep_local],
        })
    return clusters
