"""Every number in the weekly summary — computed here in SQL, never by the LLM.

Plain English: counts and comparisons for one week (how many items, the mood
split, how it moved vs last week) plus one real customer quote per top driver.
The narrator stage takes these numbers as given and only writes sentences.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text

from echo import config
from echo.money.engine import week_bounds


def _version(model: str | None, pv: str | None) -> tuple[str, str]:
    return (model or config.settings.model, pv or config.CLASSIFY_PROMPT_VERSION)


def prev_week(week: str) -> str:
    """The ISO date one week earlier (for week-over-week comparison)."""
    start, _ = week_bounds(week)
    return (start - timedelta(days=7)).strftime("%Y-%m-%d")


def _counts(engine, week: str, model: str, pv: str) -> dict:
    start, end = week_bounds(week)
    sql = text("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE a.sentiment='positive') AS positive,
               count(*) FILTER (WHERE a.sentiment='neutral')  AS neutral,
               count(*) FILTER (WHERE a.sentiment='negative') AS negative
        FROM analysis a JOIN feedback f ON f.item_id = a.item_id
        WHERE a.model_name = :model AND a.prompt_version = :pv
              AND f.created_at >= :start AND f.created_at < :end
    """)
    with engine.connect() as c:
        r = c.execute(sql, {"model": model, "pv": pv, "start": start, "end": end}).one()
    return {"total": int(r.total), "positive": int(r.positive),
            "neutral": int(r.neutral), "negative": int(r.negative)}


def volume_and_sentiment(engine, week: str, model: str | None = None, pv: str | None = None) -> dict:
    """This week's volume + sentiment split, and the week-over-week comparison."""
    model, pv = _version(model, pv)
    cur = _counts(engine, week, model, pv)
    prev = _counts(engine, prev_week(week), model, pv)
    if prev["total"] == 0:
        pct = None  # no baseline yet
    else:
        pct = round((cur["total"] - prev["total"]) / prev["total"] * 100, 1)
    neg_share = round(cur["negative"] / cur["total"] * 100, 1) if cur["total"] else 0.0
    return {"week": week, "current": cur, "previous": prev,
            "volume_pct_change": pct, "negative_share_pct": neg_share,
            "has_baseline": prev["total"] > 0}


def urgent_count(engine, week: str, model: str | None = None, pv: str | None = None) -> int:
    """How many items this week are at/above the urgency floor (>= URGENCY_FLOOR)."""
    model, pv = _version(model, pv)
    start, end = week_bounds(week)
    sql = text("""
        SELECT count(*) FROM analysis a JOIN feedback f ON f.item_id = a.item_id
        WHERE a.model_name = :model AND a.prompt_version = :pv
              AND a.urgency >= :floor AND f.created_at >= :start AND f.created_at < :end
    """)
    with engine.connect() as c:
        return int(c.execute(sql, {"model": model, "pv": pv,
                                   "floor": config.URGENCY_FLOOR, "start": start, "end": end}).scalar())


def representative_quote(engine, week: str, category: str,
                         model: str | None = None, pv: str | None = None) -> dict | None:
    """One real negative quote for a category this week (highest-value order first)."""
    model, pv = _version(model, pv)
    start, end = week_bounds(week)
    sql = text("""
        SELECT f.item_id, f.source_type, left(f.text, 220) AS quote
        FROM analysis a JOIN feedback f ON f.item_id = a.item_id
        WHERE a.model_name = :model AND a.prompt_version = :pv
              AND a.category = :cat AND a.sentiment = 'negative'
              AND f.created_at >= :start AND f.created_at < :end
              AND length(trim(f.text)) > 0
        ORDER BY f.order_value DESC NULLS LAST
        LIMIT 1
    """)
    with engine.connect() as c:
        r = c.execute(sql, {"model": model, "pv": pv, "cat": category,
                            "start": start, "end": end}).first()
    return {"item_id": r.item_id, "source_type": r.source_type, "quote": r.quote} if r else None
