"""``/users/analytics`` - company-only, per-user submission behavior.

Plain English: for every registered user, what have they told us and how has
their tone shifted over time? Feeds the admin "User Analytics" dashboard page.
Every number is SQL-computed; classification already happened at submission
time (``POST /feedback``) - this endpoint only aggregates what was stored.

Scope: only feedback rows a real account submitted count here
(``feedback.submitter_id IS NOT NULL``) - the 15k batch corpus has no
submitter and never appears. Users with zero submissions are still listed (so
the roster is complete), with their analytics fields empty/zero.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter
from sqlalchemy import text

from echo.api import deps
from echo.money import engine as money

router = APIRouter(prefix="/users", tags=["users"])

_P = {"model": deps.MODEL, "pv": deps.PROMPT_VERSION}


@router.get("/analytics")
def user_analytics() -> dict:
    eng = deps.get_engine()

    with eng.connect() as c:
        users = [dict(r._mapping) for r in c.execute(text(
            "SELECT id, email, role, created_at::text AS created_at FROM users ORDER BY created_at"
        ))]

        items = [dict(r._mapping) for r in c.execute(text("""
            SELECT f.submitter_id, f.item_id, f.created_at::text AS created_at, f.source_type,
                   a.category, a.sentiment, a.urgency, left(f.text, 160) AS snippet
            FROM analysis a JOIN feedback f ON f.item_id = a.item_id
            WHERE f.submitter_id IS NOT NULL AND a.model_name = :model AND a.prompt_version = :pv
            ORDER BY f.submitter_id, f.created_at
        """), _P)]

        weekly_rows = [dict(r._mapping) for r in c.execute(text("""
            SELECT f.submitter_id, date_trunc('week', f.created_at)::date::text AS week,
                   count(*) FILTER (WHERE a.sentiment = 'positive') AS positive,
                   count(*) FILTER (WHERE a.sentiment = 'neutral')  AS neutral,
                   count(*) FILTER (WHERE a.sentiment = 'negative') AS negative
            FROM analysis a JOIN feedback f ON f.item_id = a.item_id
            WHERE f.submitter_id IS NOT NULL AND a.model_name = :model AND a.prompt_version = :pv
            GROUP BY 1, 2 ORDER BY 1, 2
        """), _P)]

    # Group the flat SQL rows by submitter - small dataset (live submissions
    # only, not the 15k batch corpus), so doing this in Python is simpler than
    # a JSON-aggregating SQL query and easy to read.
    by_user: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_user[it["submitter_id"]].append(it)

    weekly_by_user: dict[str, list[dict]] = defaultdict(list)
    for w in weekly_rows:
        weekly_by_user[w["submitter_id"]].append({
            "week": w["week"], "positive": int(w["positive"]),
            "neutral": int(w["neutral"]), "negative": int(w["negative"]),
        })

    out_users = []
    for u in users:
        subs = by_user.get(u["id"], [])  # ascending by created_at
        n = len(subs)

        sentiment = {"positive": 0, "neutral": 0, "negative": 0}
        category_counts: dict[str, int] = {}
        for s in subs:
            sentiment[s["sentiment"]] = sentiment.get(s["sentiment"], 0) + 1
            category_counts[s["category"]] = category_counts.get(s["category"], 0) + 1

        # "Overall opinion": net sentiment in [-1, 1] - a simple, defensible
        # single number for a leaderboard column (not a modeled/LLM figure).
        net_sentiment = round((sentiment["positive"] - sentiment["negative"]) / n, 3) if n else None
        avg_urgency = round(sum(s["urgency"] for s in subs) / n, 2) if n else None

        item_ids = [s["item_id"] for s in subs]
        money_fig = money.exposure_for_items(item_ids, eng, deps.MODEL, deps.PROMPT_VERSION)

        out_users.append({
            "id": u["id"], "email": u["email"], "role": u["role"], "created_at": u["created_at"],
            "n_submissions": n,
            "sentiment": sentiment,
            "net_sentiment": net_sentiment,
            "avg_urgency": avg_urgency,
            "first_submission": subs[0]["created_at"] if subs else None,
            "last_submission": subs[-1]["created_at"] if subs else None,
            "category_counts": category_counts,
            "direct_exposure": money_fig["direct_exposure"],
            "retention": money_fig["retention"],  # low/base/high - never a single modeled number
            "revenue_at_risk": money_fig["revenue_at_risk"],
            "sentiment_over_time": weekly_by_user.get(u["id"], []),
            "submissions": [
                {"item_id": s["item_id"], "created_at": s["created_at"], "source_type": s["source_type"],
                 "category": s["category"], "sentiment": s["sentiment"], "urgency": s["urgency"],
                 "snippet": s["snippet"]}
                for s in reversed(subs)  # most recent first for display
            ],
        })

    out_users.sort(key=lambda u: u["n_submissions"], reverse=True)

    return {
        "totals": {
            "users": len(users),
            "users_with_submissions": sum(1 for u in out_users if u["n_submissions"] > 0),
            "submissions": len(items),
        },
        "users": out_users,
    }
