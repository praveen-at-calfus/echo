"""``GET /eval/gold`` — model-evaluation report card: gold-set confusion matrix
(40 human-verified items) + silver-sentiment accuracy at scale (thousands of
star/NPS-scored items). Pure SQL/Python aggregates — no LLM call, no new table.
"""

from __future__ import annotations

from fastapi import APIRouter

from echo.api import deps
from echo.classify import evaluate

router = APIRouter(prefix="/eval", tags=["eval"])


@router.get("/gold")
def gold() -> dict:
    eng = deps.get_engine()
    return {
        "gold": evaluate.gold_report(eng, deps.MODEL, deps.PROMPT_VERSION),
        "silver_sentiment": evaluate.silver_sentiment_report(eng, deps.MODEL, deps.PROMPT_VERSION),
    }
