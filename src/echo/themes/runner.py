"""Themes engine: cluster a week, rank clusters by money, label the top ones.

Plain English: for one week, group actionable feedback by meaning, attach each
group's revenue-at-risk (from the money engine), keep the top 10, and have the
model write a specific label for each. Owner = the team behind the group's
majority category. Every number is SQL; the LLM only writes the label.

Technically: reuses cluster.py (vector math), money.engine.exposure_for_items
(ranking), and the classify/summary structured-output + audit patterns. Writes
``themes`` + ``theme_members`` idempotently (re-running a week replaces it).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from sqlalchemy import and_, delete, insert, select

from echo import config
from echo.db import schema
from echo.money import engine as money
from echo.themes import cluster
from echo.themes import label as labeller
from echo.themes.prompts import ThemeLabel


def _item_meta(engine, item_ids: list[str], model: str, pv: str) -> dict[str, dict]:
    a, f = schema.analysis, schema.feedback
    q = (select(f.c.item_id, a.c.category, f.c.text, f.c.order_value)
         .select_from(a.join(f, f.c.item_id == a.c.item_id))
         .where(and_(a.c.model_name == model, a.c.prompt_version == pv, f.c.item_id.in_(item_ids))))
    with engine.connect() as c:
        return {r.item_id: {"category": r.category, "text": r.text,
                            "order_value": float(r.order_value or 0)} for r in c.execute(q)}


def _quotes(members: list[dict], rep_id: str) -> list[str]:
    """Representative first, then highest-value others; truncated for the prompt."""
    ordered = sorted(members, key=lambda m: (m["item_id"] != rep_id, -m["order_value"]))
    out, seen = [], set()
    for m in ordered:
        t = (m["text"] or "").strip().replace("\n", " ")[:200]
        if t and t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= config.THEME_LABEL_QUOTES:
            break
    return out


def run(week: str | None = None, threshold: float | None = None) -> dict:
    week = week or config.DEMO_WEEK
    if config.settings.use_offline:
        raise SystemExit("themes labelling needs OPENAI_API_KEY (set it in .env).")
    model = config.settings.model
    class_pv = config.CLASSIFY_PROMPT_VERSION
    engine = money._engine()
    schema.metadata.create_all(engine)

    clusters = cluster.cluster_week(engine, week, model, class_pv, threshold)
    if not clusters:
        print(f"themes: week {week} — no clusters of >= {config.MIN_CLUSTER_SIZE} actionable items.")
        return {"week": week, "themes": 0}

    # attach money + majority category + representative to each cluster
    all_ids = [i for cl in clusters for i in cl["item_ids"]]
    meta = _item_meta(engine, all_ids, model, class_pv)
    for cl in clusters:
        members = [{"item_id": i, **meta[i]} for i in cl["item_ids"] if i in meta]
        cl["members"] = members
        cl["category"] = Counter(m["category"] for m in members).most_common(1)[0][0]
        cl["money"] = money.exposure_for_items(cl["item_ids"], engine, model, class_pv)
        cl["quotes"] = _quotes(members, cl["representative_item_id"])
    clusters.sort(key=lambda c: c["money"]["revenue_at_risk"], reverse=True)
    top = clusters[:config.TOP_THEMES]

    # label the kept clusters
    from langchain_openai import ChatOpenAI
    structured = ChatOpenAI(model=model, temperature=config.THEME_TEMPERATURE, seed=config.SEED,
                            api_key=config.settings.openai_api_key).with_structured_output(
        ThemeLabel, include_raw=True)
    tot_in = tot_out = generic_n = 0
    for cl in top:
        res = labeller.label_cluster(structured, cl["category"], cl["quotes"])
        cl["label"] = res["label"]
        cl["_llm"] = res
        tot_in += res["in_t"]
        tot_out += res["out_t"]
        generic_n += int(res["generic"])

    # write themes + theme_members (idempotent: replace the week)
    t, tm = schema.themes, schema.theme_members
    week_date = datetime.strptime(week, "%Y-%m-%d").date()
    with engine.begin() as c:
        c.execute(delete(t).where(t.c.week_start == week_date))  # cascades to theme_members
        for cl in top:
            m = cl["money"]
            rep_text = meta.get(cl["representative_item_id"], {}).get("text", "")
            res = c.execute(insert(t).values(
                week_start=week_date, label=cl["label"], category=cl["category"],
                owner_team=config.CATEGORY_OWNER.get(cl["category"], "Triage"),
                item_count=cl["size"], direct_exposure=m["direct_exposure"],
                retention_risk_low=m["retention"]["low"], retention_risk_base=m["retention"]["base"],
                retention_risk_high=m["retention"]["high"], revenue_at_risk=m["revenue_at_risk"],
                representative_quote=(rep_text or "")[:500],
                representative_item_id=cl["representative_item_id"]))
            theme_id = res.inserted_primary_key[0]
            c.execute(insert(tm), [{"theme_id": theme_id, "item_id": i} for i in cl["item_ids"]])
            c.execute(insert(schema.llm_calls), {
                "item_id": None, "call_type": "theme_label", "model_name": model,
                "prompt_version": config.THEME_PROMPT_VERSION, "input": cl["category"],
                "output": cl["label"], "prompt_tokens": cl["_llm"]["in_t"] or None,
                "completion_tokens": cl["_llm"]["out_t"] or None,
                "total_tokens": (cl["_llm"]["in_t"] + cl["_llm"]["out_t"]) or None,
                "latency_ms": cl["_llm"]["latency_ms"] or None, "status": "ok", "error": None})

    cost = tot_in / 1e6 * 0.15 + tot_out / 1e6 * 0.60
    _report(week, clusters, top, generic_n, cost)
    return {"week": week, "clusters": len(clusters), "themes": len(top),
            "generic_labels": generic_n, "tokens_in": tot_in, "tokens_out": tot_out,
            "est_cost": round(cost, 4)}


def _report(week, clusters, top, generic_n, cost) -> None:
    print(f"\n=== Themes · week of {week} · {len(clusters)} clusters "
          f"(>= {config.MIN_CLUSTER_SIZE}) · labelled top {len(top)} ===\n")
    for i, cl in enumerate(top, 1):
        m = cl["money"]
        flag = " ⚠generic" if cl["_llm"]["generic"] else ""
        print(f"{i:>2}. {cl['label']}{flag}")
        print(f"      [{cl['category']} · {config.CATEGORY_OWNER.get(cl['category'], 'Triage')}] "
              f"{cl['size']} items · at-risk R${m['revenue_at_risk']:,.0f} "
              f"(direct R${m['direct_exposure']:,.0f} + retention base R${m['retention']['base']:,.0f})")
        print(f"      e.g. {cl['quotes'][0][:110]!r}")
    print(f"\n(est. ${cost:.4f} · {generic_n} label(s) still generic after retries · "
          f"clustering + ranking are $0/SQL)\n")
