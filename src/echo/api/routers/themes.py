"""``GET /themes`` — weekly themes ranked by revenue-at-risk."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import text

from echo.api import deps

router = APIRouter(tags=["themes"])


@router.get("/themes")
def themes(week: str | None = Query(None, description="ISO week start; default = latest available"),
           limit: int = Query(10, ge=1, le=50)) -> dict:
    eng = deps.get_engine()
    with eng.connect() as c:
        if week is None:
            week = c.execute(text("SELECT max(week_start)::text FROM themes")).scalar()
        if week is None:
            return {"week": None, "themes": []}
        rows = c.execute(text("""
            SELECT label, category, owner_team, item_count, direct_exposure,
                   retention_risk_low, retention_risk_base, retention_risk_high,
                   revenue_at_risk, representative_quote, representative_item_id
            FROM themes WHERE week_start = :week
            ORDER BY revenue_at_risk DESC LIMIT :limit
        """), {"week": week, "limit": limit}).all()
    return {"week": week, "themes": [{
        "label": r.label, "category": r.category, "owner": r.owner_team,
        "item_count": int(r.item_count or 0), "direct_exposure": float(r.direct_exposure or 0),
        "retention": {"low": float(r.retention_risk_low or 0), "base": float(r.retention_risk_base or 0),
                      "high": float(r.retention_risk_high or 0)},
        "revenue_at_risk": float(r.revenue_at_risk or 0),
        "representative_quote": r.representative_quote, "representative_item_id": r.representative_item_id,
    } for r in rows]}
