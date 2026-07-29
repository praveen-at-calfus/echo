"""``/summary/weekly`` — generate a weekly narrative live (POST) or read one back (GET).

POST is the primary path: the user picks a week, echo computes every number in
SQL, the LLM narrates once, and the row is upserted — generate-then-store, not
a passive read of whatever happened to be pre-computed. GET still exists for
reading back a previously generated week (e.g. the demo week pre-seeded before
an OPENAI_API_KEY was set) without paying for another LLM call.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from echo.api import deps
from echo.api.schemas import WeeklySummaryIn

router = APIRouter(prefix="/summary", tags=["summary"])

_ROW_SQL = """
    SELECT week_start::text AS week_start, tldr, narrative, volume_total, volume_prev,
           sentiment_positive, sentiment_neutral, sentiment_negative,
           top_themes, urgent_items, recommended_actions, model_name, prompt_version
    FROM weekly_summary WHERE week_start = :week
"""


def _fetch(eng, week: str) -> dict | None:
    """Look up the previously generated weekly summary row for the given week, or return None if none exists yet."""
    with eng.connect() as c:
        r = c.execute(text(_ROW_SQL), {"week": week}).mappings().first()
    return dict(r) if r else None


@router.get("/weekly")
def weekly(week: str | None = Query(None, description="ISO week start; default = latest available")) -> dict:
    """GET /summary/weekly: read back a previously generated weekly summary, defaulting to the most recent week that has one, without calling the LLM again."""
    eng = deps.get_engine()
    if week is None:
        with eng.connect() as c:
            week = c.execute(text("SELECT max(week_start)::text FROM weekly_summary")).scalar()
        if week is None:
            raise HTTPException(404, "no weekly summary has been generated yet")
    row = _fetch(eng, week)
    if row is None:
        raise HTTPException(404, f"no weekly summary for week {week}")
    return row


@router.post("/weekly")
def generate_weekly(body: WeeklySummaryIn) -> dict:
    """POST /summary/weekly: generate a new weekly summary live (SQL computes the numbers, the LLM only writes the narrative), store it, and return the resulting row."""
    if not deps.llm_available():
        raise HTTPException(503, "generating a weekly summary needs an OpenAI key (set OPENAI_API_KEY)")
    from echo.summary.runner import run as run_summary

    eng = deps.get_engine()
    try:
        run_summary(week=body.week, engine=eng)
    except SystemExit as e:
        # run_summary raises SystemExit for user-facing problems (e.g. a week
        # with no data to summarize); convert that into a proper HTTP error
        # instead of letting it crash the server process.
        raise HTTPException(422, str(e)) from e
    row = _fetch(eng, body.week)
    if row is None:
        raise HTTPException(500, f"summary generation for {body.week} did not produce a row")
    return row
