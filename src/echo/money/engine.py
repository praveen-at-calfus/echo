"""The money-weighting engine — every figure computed in SQL, none by the LLM.

Plain English: this turns labeled complaints into money. It produces two clearly
separated numbers per category (the separation *is* the credibility):

  * **Direct Exposure** — real dollars from real fields (a refund pending, a
    disputed charge, the value of a lost shipment, return cost, support-contact
    cost). Deterministic, negatives only.
  * **Retention Risk** — a *modeled* estimate of revenue at risk from customers
    who might churn: customer_value x churn_uplift x category_propensity, one
    at-risk customer counted once on their worst issue, reported as a low/base/
    high range (never a false-precision single number).

Plus an item-impact ranking primitive (severity x value x sentiment) and a
coverage tier (T0-T3) saying how much the current data actually supports.

Technically: parameterized SQL CTEs over ``analysis ⋈ feedback`` (SQLAlchemy
Core ``text``), the per-category mechanics coming from :mod:`money.mechanics`,
all knobs from :mod:`config`. Returns plain dicts — no LLM, no new table; the
API/summary stages call these functions and inject the results into prose.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine, text

from echo import config
from echo.money import mechanics

_COMPONENTS = ("refund_pending", "disputed_charge", "lost_order_value", "return_cost", "wismo_cost")


def _engine(engine=None):
    return engine or create_engine(config.settings.database_url)


def _version(model: str | None, pv: str | None) -> tuple[str, str]:
    return (model or config.settings.model, pv or config.CLASSIFY_PROMPT_VERSION)


def week_bounds(week: str | None) -> tuple[datetime, datetime] | None:
    """(start, start+7d) for a 'YYYY-MM-DD' week, or None for all-time."""
    if not week:
        return None
    start = datetime.strptime(week, "%Y-%m-%d")
    return start, start + timedelta(days=7)


def coverage_tier(engine=None) -> str:
    """Which money tier the current data supports (T0 text-only .. T3 full money)."""
    eng = _engine(engine)
    with eng.connect() as c:
        r = c.execute(text("""
            SELECT count(*) n, count(order_value) has_val, count(refund_amount) has_refund,
                   count(customer_unique_id) has_cust, count(source_score) has_score
            FROM feedback
        """)).one()
    if r.n == 0:
        return "T0"
    if r.has_val and r.has_cust and r.has_refund:
        return "T3"
    if r.has_val:
        return "T2"
    if r.has_score:
        return "T1"
    return "T0"


def category_breakdown(engine=None, week: str | None = None,
                       model: str | None = None, pv: str | None = None) -> list[dict]:
    """Per-category volume, item-impact, and Direct-Exposure breakdown (negatives only)."""
    eng = _engine(engine)
    model, pv = _version(model, pv)
    sev = mechanics.severity_sql("a.urgency")
    smult = mechanics.sentiment_sql("a.sentiment")
    comp = mechanics.direct_components("a.category", "f.order_value", "f.refund_amount", "f.fulfillment_outcome")
    comp_select = ",\n           ".join(f"({sql}) AS {name}" for name, sql in comp.items())
    comp_agg = ",\n          ".join(
        f"COALESCE(sum({name}) FILTER (WHERE sentiment='negative'),0) AS {name}" for name in _COMPONENTS)
    bounds = week_bounds(week)
    wk = "AND f.created_at >= :wk_start AND f.created_at < :wk_end" if bounds else ""

    sql = text(f"""
        WITH base AS (
          SELECT a.category AS category, a.sentiment AS sentiment,
                 ({sev}) AS sev, ({smult}) AS smult, COALESCE(f.order_value,1) AS value,
                 {comp_select}
          FROM analysis a JOIN feedback f ON f.item_id = a.item_id
          WHERE a.model_name = :model AND a.prompt_version = :pv {wk}
        )
        SELECT category,
          count(*) AS n,
          count(*) FILTER (WHERE sentiment='negative') AS n_neg,
          COALESCE(sum(sev*value*smult),0) AS impact,
          {comp_agg}
        FROM base GROUP BY category
    """)
    params = {"model": model, "pv": pv}
    if bounds:
        params["wk_start"], params["wk_end"] = bounds
    with eng.connect() as c:
        rows = [dict(r._mapping) for r in c.execute(sql, params)]

    out = []
    for r in rows:
        components = {k: float(r[k]) for k in _COMPONENTS}
        out.append({
            "category": r["category"],
            "owner": config.CATEGORY_OWNER.get(r["category"], "Triage"),
            "n": int(r["n"]),
            "n_neg": int(r["n_neg"]),
            "impact": round(float(r["impact"]), 2),
            "direct_exposure": round(sum(components.values()), 2),
            "components": {k: round(v, 2) for k, v in components.items()},
        })
    out.sort(key=lambda d: d["direct_exposure"], reverse=True)
    return out


def retention_by_category(engine=None, week: str | None = None,
                          model: str | None = None, pv: str | None = None) -> dict[str, dict]:
    """Per-category modeled Retention Risk (low/base/high) + at-risk customer count.

    One at-risk customer is counted once, on their worst (highest-urgency) issue.
    """
    eng = _engine(engine)
    model, pv = _version(model, pv)
    prop = mechanics.propensity_sql("a.category")
    bounds = week_bounds(week)
    wk = "AND f.created_at >= :wk_start AND f.created_at < :wk_end" if bounds else ""

    sql = text(f"""
        WITH worst AS (
          SELECT DISTINCT ON (f.customer_unique_id)
                 a.category AS category,
                 COALESCE(f.order_value * :annual, :flat) AS cust_value,
                 ({prop}) AS propensity
          FROM analysis a JOIN feedback f ON f.item_id = a.item_id
          WHERE a.sentiment='negative' AND a.model_name = :model AND a.prompt_version = :pv
                AND f.customer_unique_id IS NOT NULL {wk}
          ORDER BY f.customer_unique_id, a.urgency DESC, f.order_value DESC NULLS LAST
        )
        SELECT category, count(*) AS at_risk, COALESCE(sum(cust_value*propensity),0) AS unit
        FROM worst GROUP BY category
    """)
    params = {"model": model, "pv": pv,
              "annual": config.EXPECTED_ANNUAL_ORDERS, "flat": config.FLAT_CUSTOMER_VALUE}
    if bounds:
        params["wk_start"], params["wk_end"] = bounds
    with eng.connect() as c:
        rows = c.execute(sql, params).all()

    up = config.CHURN_UPLIFT
    out: dict[str, dict] = {}
    for r in rows:
        unit = float(r.unit)  # = sum(customer_value * category_propensity)
        out[r.category] = {
            "at_risk": int(r.at_risk),
            "low": round(unit * up["low"], 2),
            "base": round(unit * up["base"], 2),
            "high": round(unit * up["high"], 2),
        }
    return out


def urgent_items(engine=None, week: str | None = None, limit: int = 20,
                 model: str | None = None, pv: str | None = None) -> list[dict]:
    """Urgent queue (urgency >= URGENCY_FLOOR) ranked by per-item Direct Exposure.

    Reused by the weekly summary and the API's ``/urgent`` endpoint.
    """
    eng = _engine(engine)
    model, pv = _version(model, pv)
    comp = mechanics.direct_components("a.category", "f.order_value", "f.refund_amount", "f.fulfillment_outcome")
    exposure = " + ".join(f"({sql})" for sql in comp.values())
    bounds = week_bounds(week)
    wk = "AND f.created_at >= :wk_start AND f.created_at < :wk_end" if bounds else ""

    sql = text(f"""
        SELECT f.item_id, a.category, a.sentiment, a.urgency, f.source_type,
               COALESCE(f.order_value,0) AS order_value,
               ({exposure}) AS exposure,
               left(f.text, 160) AS snippet
        FROM analysis a JOIN feedback f ON f.item_id = a.item_id
        WHERE a.model_name = :model AND a.prompt_version = :pv
              AND a.urgency >= :floor {wk}
        ORDER BY ({exposure}) DESC, f.order_value DESC NULLS LAST, a.urgency DESC
        LIMIT :limit
    """)
    params = {"model": model, "pv": pv, "floor": config.URGENCY_FLOOR, "limit": limit}
    if bounds:
        params["wk_start"], params["wk_end"] = bounds
    with eng.connect() as c:
        rows = [dict(r._mapping) for r in c.execute(sql, params)]
    for r in rows:
        r["order_value"] = round(float(r["order_value"]), 2)
        r["exposure"] = round(float(r["exposure"]), 2)
        r["owner"] = config.CATEGORY_OWNER.get(r["category"], "Triage")
    return rows


def summary(engine=None, week: str | None = None,
            model: str | None = None, pv: str | None = None) -> dict:
    """Full money report: totals, per-category exposure, modeled retention range, tier."""
    eng = _engine(engine)
    cats = category_breakdown(eng, week, model, pv)
    ret = retention_by_category(eng, week, model, pv)
    tier = coverage_tier(eng)

    for c in cats:
        c["retention"] = ret.get(c["category"], {"at_risk": 0, "low": 0.0, "base": 0.0, "high": 0.0})

    totals = {
        "items": sum(c["n"] for c in cats),
        "negatives": sum(c["n_neg"] for c in cats),
        "impact": round(sum(c["impact"] for c in cats), 2),
        "direct_exposure": round(sum(c["direct_exposure"] for c in cats), 2),
        "at_risk_customers": sum(r["at_risk"] for r in ret.values()),
        "retention": {
            tier_k: round(sum(r[tier_k] for r in ret.values()), 2) for tier_k in ("low", "base", "high")
        },
    }
    return {"week": week or "all-time", "tier": tier, "currency": "BRL",
            "totals": totals, "categories": cats}
