"""``/stats/*`` — dashboard aggregates. All numbers computed in SQL.

overview (KPI tiles + tier) · volume?by=category|source · sentiment?by=split|week
· crosstab (category × source).
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import text

from echo import config
from echo.api import deps
from echo.money import engine as money

router = APIRouter(prefix="/stats", tags=["stats"])

# analysis ⋈ feedback for the active version — the base every aggregate filters on.
_BASE = """
  FROM analysis a JOIN feedback f ON f.item_id = a.item_id
  WHERE a.model_name = :model AND a.prompt_version = :pv
"""
_P = {"model": deps.MODEL, "pv": deps.PROMPT_VERSION}


@router.get("/overview")
def overview() -> dict:
    """KPI tiles: volume, sentiment split, urgent count, exposure, retention range, tier."""
    eng = deps.get_engine()
    rep = money.summary(eng)  # totals + per-category + retention + tier
    with eng.connect() as c:
        s = c.execute(text(f"""
            SELECT count(*) FILTER (WHERE a.sentiment='positive') pos,
                   count(*) FILTER (WHERE a.sentiment='neutral')  neu,
                   count(*) FILTER (WHERE a.sentiment='negative') neg,
                   count(*) FILTER (WHERE a.urgency >= :floor)    urgent
            {_BASE}
        """), _P | {"floor": config.URGENCY_FLOOR}).one()
    t = rep["totals"]
    return {
        "items": t["items"], "tier": rep["tier"], "currency": rep["currency"],
        "sentiment": {"positive": int(s.pos), "neutral": int(s.neu), "negative": int(s.neg)},
        "negative_share_pct": round(int(s.neg) / t["items"] * 100, 1) if t["items"] else 0.0,
        "urgent": int(s.urgent),
        "direct_exposure": t["direct_exposure"],
        "retention": t["retention"], "at_risk_customers": t["at_risk_customers"],
    }


@router.get("/volume")
def volume(by: str = Query("category", pattern="^(category|source)$")) -> dict:
    """GET /stats/volume: count of feedback items grouped either by category or by source type (review/ticket/survey), depending on the `by` parameter."""
    col = "a.category" if by == "category" else "f.source_type"
    with deps.get_engine().connect() as c:
        rows = c.execute(text(f"SELECT {col} AS k, count(*) n {_BASE} GROUP BY 1 ORDER BY 2 DESC"), _P).all()
    return {"by": by, "data": [{"key": r.k, "count": int(r.n)} for r in rows]}


@router.get("/sentiment")
def sentiment(by: str = Query("split", pattern="^(split|week)$")) -> dict:
    """GET /stats/sentiment: either the overall positive/neutral/negative split, or the week-by-week sentiment trend, depending on the `by` parameter."""
    if by == "split":
        with deps.get_engine().connect() as c:
            rows = c.execute(text(f"SELECT a.sentiment AS k, count(*) n {_BASE} GROUP BY 1"), _P).all()
        return {"by": "split", "data": {r.k: int(r.n) for r in rows}}
    # weekly sentiment trend
    with deps.get_engine().connect() as c:
        rows = c.execute(text(f"""
            SELECT date_trunc('week', f.created_at)::date AS week,
                   count(*) FILTER (WHERE a.sentiment='positive') pos,
                   count(*) FILTER (WHERE a.sentiment='neutral')  neu,
                   count(*) FILTER (WHERE a.sentiment='negative') neg
            {_BASE} GROUP BY 1 ORDER BY 1
        """), _P).all()
    return {"by": "week", "data": [{"week": str(r.week), "positive": int(r.pos),
                                    "neutral": int(r.neu), "negative": int(r.neg)} for r in rows]}


@router.get("/crosstab")
def crosstab() -> dict:
    """Category × source counts (e.g. which categories are ticket- vs survey-heavy)."""
    with deps.get_engine().connect() as c:
        rows = c.execute(text(f"SELECT a.category AS cat, f.source_type AS src, count(*) n "
                              f"{_BASE} GROUP BY 1,2"), _P).all()
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        out.setdefault(r.cat, {})[r.src] = int(r.n)
    return {"data": out}
