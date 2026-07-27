"""``GET /summary/weekly`` — the stored weekly narrative (latest, or a given week)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from echo.api import deps

router = APIRouter(prefix="/summary", tags=["summary"])


@router.get("/weekly")
def weekly(week: str | None = Query(None, description="ISO week start; default = latest available")) -> dict:
    eng = deps.get_engine()
    with eng.connect() as c:
        if week is None:
            week = c.execute(text("SELECT max(week_start)::text FROM weekly_summary")).scalar()
        if week is None:
            raise HTTPException(404, "no weekly summary has been generated yet")
        r = c.execute(text("""
            SELECT week_start::text AS week_start, tldr, narrative, volume_total, volume_prev,
                   sentiment_positive, sentiment_neutral, sentiment_negative,
                   top_themes, urgent_items, recommended_actions, model_name, prompt_version
            FROM weekly_summary WHERE week_start = :week
        """), {"week": week}).mappings().first()
    if r is None:
        raise HTTPException(404, f"no weekly summary for week {week}")
    return dict(r)
