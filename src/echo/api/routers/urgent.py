"""``GET /urgent`` — the urgent queue (urgency >= floor) ranked by $ exposure."""

from __future__ import annotations

from fastapi import APIRouter, Query

from echo.api import deps
from echo.money import engine as money

router = APIRouter(tags=["urgent"])


@router.get("/urgent")
def urgent(week: str | None = Query(None, description="ISO week start; default = all-time"),
           limit: int = Query(20, ge=1, le=100)) -> dict:
    items = money.urgent_items(deps.get_engine(), week=week, limit=limit)
    return {"week": week or "all-time", "count": len(items), "items": items}
