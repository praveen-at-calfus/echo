"""Weekly-summary engine: gather SQL numbers, let the LLM narrate, write the row.

Plain English: for one week, echo computes every number (volume, sentiment vs
last week, the top money drivers, the urgent queue), asks the model to write a
short briefing + 3 recommended actions around those numbers, then attaches the
dollar figures and owning teams itself before saving. The model never emits a
figure; it only chooses which driver each action targets and writes the prose.

Technically: reuses the money engine (drivers + urgent items) and the classify
runner's structured-output + audit patterns. Writes one row per week to
``weekly_summary`` (upsert on ``week_start``) + an ``llm_calls`` audit row.
"""

from __future__ import annotations

import time
from datetime import datetime

from sqlalchemy import create_engine, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tenacity import retry, stop_after_attempt, wait_exponential

from echo import config
from echo.db import schema
from echo.money import engine as money
from echo.summary import sql
from echo.summary.prompts import WeeklyNarrative, build_messages

_PRICE_IN, _PRICE_OUT = 0.15, 0.60  # gpt-4o-mini USD per 1M tokens


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=20), reraise=True)
def _invoke(structured_llm, facts: dict):
    t0 = time.perf_counter()
    out = structured_llm.invoke(build_messages(facts))
    latency = int((time.perf_counter() - t0) * 1000)
    parsed = out.get("parsed")
    if parsed is None:
        raise ValueError(f"structured parse failed: {out.get('parsing_error')}")
    raw = out.get("raw")
    um = getattr(raw, "usage_metadata", None) or {}
    return parsed, int(um.get("input_tokens", 0)), int(um.get("output_tokens", 0)), latency


def _drivers(eng, week: str, n: int) -> list[dict]:
    """Top-N money drivers this week. Uses real themes if present, else categories
    by revenue-at-risk (Direct Exposure + modeled Retention base) as the fallback."""
    t = schema.themes
    week_date = datetime.strptime(week, "%Y-%m-%d").date()
    with eng.connect() as c:
        theme_rows = c.execute(
            select(t.c.label, t.c.category, t.c.owner_team, t.c.item_count,
                   t.c.direct_exposure, t.c.retention_risk_low, t.c.retention_risk_base,
                   t.c.retention_risk_high, t.c.revenue_at_risk, t.c.representative_quote)
            .where(t.c.week_start == week_date).order_by(t.c.revenue_at_risk.desc()).limit(n)
        ).all()
    if theme_rows:
        return [{"source": "theme", "label": r.label, "category": r.category,
                 "owner": r.owner_team, "item_count": int(r.item_count or 0),
                 "direct_exposure": float(r.direct_exposure or 0),
                 "retention": {"low": float(r.retention_risk_low or 0),
                               "base": float(r.retention_risk_base or 0),
                               "high": float(r.retention_risk_high or 0)},
                 "revenue_at_risk": float(r.revenue_at_risk or 0),
                 "quote": r.representative_quote} for r in theme_rows]

    # fallback: categories by revenue-at-risk
    rep = money.summary(eng, week=week)
    cats = []
    for c in rep["categories"]:
        rar = round(c["direct_exposure"] + c["retention"]["base"], 2)
        if rar <= 0:
            continue
        q = sql.representative_quote(eng, week, c["category"])
        cats.append({"source": "category_fallback", "label": c["category"], "category": c["category"],
                     "owner": c["owner"], "item_count": c["n_neg"],
                     "direct_exposure": c["direct_exposure"], "retention": c["retention"],
                     "revenue_at_risk": rar, "quote": (q or {}).get("quote")})
    cats.sort(key=lambda d: d["revenue_at_risk"], reverse=True)
    return cats[:n]


def run(week: str | None = None) -> dict:
    week = week or config.DEMO_WEEK
    if config.settings.use_offline:
        raise SystemExit("summary needs OPENAI_API_KEY (set it in .env).")
    model = config.settings.model
    pv = config.SUMMARY_PROMPT_VERSION
    eng = create_engine(config.settings.database_url)
    schema.metadata.create_all(eng)

    # 1) every number, from SQL
    vs = sql.volume_and_sentiment(eng, week)
    drivers = _drivers(eng, week, config.SUMMARY_TOP_DRIVERS)
    urgent = money.urgent_items(eng, week=week, limit=config.SUMMARY_URGENT_LIMIT)
    urgent_total = sql.urgent_count(eng, week)
    if vs["current"]["total"] == 0:
        raise SystemExit(f"summary: no analysed items in week {week} — nothing to summarise.")

    # 2) compact facts for the narrator (no figure is the model's to invent)
    facts = {
        "week": week, "currency": "BRL",
        "volume": {"this_week": vs["current"]["total"], "last_week": vs["previous"]["total"],
                   "pct_change": vs["volume_pct_change"], "has_baseline": vs["has_baseline"]},
        "sentiment": {k: vs["current"][k] for k in ("positive", "neutral", "negative")},
        "negative_share_pct": vs["negative_share_pct"],
        "urgent_count": urgent_total,
        "top_drivers": [{"category": d["label"], "owner": d["owner"], "negatives": d["item_count"],
                         "direct_exposure": d["direct_exposure"],
                         "retention_base": d["retention"]["base"],
                         "revenue_at_risk": d["revenue_at_risk"],
                         "example_quote": d["quote"]} for d in drivers],
        "drivers_are": "categories (themes not yet computed)" if drivers and drivers[0]["source"] == "category_fallback" else "themes",
    }

    # 3) narrate
    from langchain_openai import ChatOpenAI
    structured = ChatOpenAI(model=model, temperature=config.SUMMARY_TEMPERATURE, seed=config.SEED,
                            api_key=config.settings.openai_api_key).with_structured_output(
        WeeklyNarrative, include_raw=True)
    narrative, in_t, out_t, lat = _invoke(structured, facts)

    # 4) enrich each action with the SQL dollar figure + owner (LLM supplied neither).
    # Use the authoritative per-CATEGORY money (a category may span several theme drivers),
    # so the action's $ is the whole category's exposure, not one theme's slice.
    cat_money = {c["category"]: c for c in money.summary(eng, week=week)["categories"]}
    actions = []
    for a in narrative.actions:
        cm = cat_money.get(a.category, {})
        direct = cm.get("direct_exposure", 0.0)
        base = cm.get("retention", {}).get("base", 0.0)
        actions.append({
            "category": a.category, "recommendation": a.recommendation,
            "owner": config.CATEGORY_OWNER.get(a.category, "Triage"),
            "direct_exposure": direct, "retention_base": base,
            "revenue_at_risk": round(direct + base, 2),
        })

    # 5) write the row (upsert on week) + audit
    ws = schema.weekly_summary
    row = {
        "week_start": datetime.strptime(week, "%Y-%m-%d").date(),
        "tldr": narrative.tldr, "narrative": narrative.narrative,
        "volume_total": vs["current"]["total"], "volume_prev": vs["previous"]["total"],
        "sentiment_positive": vs["current"]["positive"], "sentiment_neutral": vs["current"]["neutral"],
        "sentiment_negative": vs["current"]["negative"],
        "top_themes": drivers, "urgent_items": urgent, "recommended_actions": actions,
        "model_name": model, "prompt_version": pv,
    }
    with eng.begin() as c:
        stmt = pg_insert(ws).values(**row).on_conflict_do_update(
            index_elements=[ws.c.week_start],
            set_={k: row[k] for k in row if k != "week_start"} | {"created_at": func.now()})
        c.execute(stmt)
        c.execute(insert(schema.llm_calls), {
            "item_id": None, "call_type": "summary", "model_name": model, "prompt_version": pv,
            "input": f"weekly facts for {week}"[:2000], "output": narrative.tldr[:2000],
            "prompt_tokens": in_t or None, "completion_tokens": out_t or None,
            "total_tokens": (in_t + out_t) or None, "latency_ms": lat or None,
            "status": "ok", "error": None})

    cost = in_t / 1e6 * _PRICE_IN + out_t / 1e6 * _PRICE_OUT
    _print_report(week, facts, narrative, actions, cost)
    return {"week": week, "drivers": len(drivers), "urgent": urgent_total,
            "tokens_in": in_t, "tokens_out": out_t, "est_cost": round(cost, 4)}


def _fmt(n) -> str:
    return f"R${float(n):,.0f}"


def _print_report(week, facts, narrative, actions, cost) -> None:
    v = facts["volume"]
    trend = "no baseline yet" if not v["has_baseline"] else f"{v['pct_change']:+.1f}% vs last week"
    print(f"\n=== Weekly summary · week of {week} ({trend}) ===\n")
    print(f"TL;DR: {narrative.tldr}\n")
    print(narrative.narrative + "\n")
    print(f"Volume {v['this_week']} ({trend}) · sentiment "
          f"+{facts['sentiment']['positive']}/~{facts['sentiment']['neutral']}/-{facts['sentiment']['negative']}"
          f" · {facts['urgent_count']} urgent (≥{config.URGENCY_FLOOR})\n")
    print("Top drivers by revenue-at-risk:")
    for d in facts["top_drivers"]:
        print(f"  • {d['category']:<26} {d['negatives']:>4} neg · direct {_fmt(d['direct_exposure'])}"
              f" · retention(base) {_fmt(d['retention_base'])} · owner {d['owner']}")
    print("\n3 recommended actions:")
    for i, a in enumerate(actions, 1):
        print(f"  {i}. [{a['owner']}] {a['recommendation']} "
              f"(~{_fmt(a['revenue_at_risk'])} at risk)")
    print(f"\n(est. ${cost:.4f} · numbers are SQL-computed; the model only narrated)\n")
