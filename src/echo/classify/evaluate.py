"""Gold-set + silver-label evaluation: how well classify's actual output matches
ground truth, at two different scales.

Plain English: two report cards, not one, because they check different things.
The GOLD report compares the classifier's category/sentiment/urgency against the
40 hand-labeled items (human-verified in ``gold_candidates`` — see its
``labeler_id`` column) — the only real ground truth for CATEGORY, since category
has no natural proxy anywhere else in the data. The SILVER report reuses the
sentiment cross-check already computed for every classified item that carries a
star/NPS score (thousands of items, not just 40) — weaker per-item ground truth
(a score, not a person), but a much larger sample.

Technically: both are pure SQL/Python aggregates over already-classified data —
no LLM call, no new table, the anti-hallucination invariant holds even for the
eval numbers themselves. Reused by the ``/eval/gold`` API endpoint and the
Streamlit "Model Evaluation" page.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text

from echo import config


def _version(model: str | None, pv: str | None) -> tuple[str, str]:
    """Fill in the current default model name and prompt version for any argument left as None."""
    return (model or config.settings.model, pv or config.CLASSIFY_PROMPT_VERSION)


def gold_report(engine=None, model: str | None = None, pv: str | None = None) -> dict:
    """Category confusion matrix + sentiment/urgency accuracy against the 40 gold items."""
    eng = engine or create_engine(config.settings.database_url)
    model, pv = _version(model, pv)
    sql = text("""
        SELECT g.item_id, g.source_type, g.gold_category, g.gold_sentiment, g.gold_urgency,
               g.labeler_notes, left(g.text, 200) AS snippet,
               a.category AS pred_category, a.sentiment AS pred_sentiment, a.urgency AS pred_urgency
        FROM gold_candidates g
        JOIN analysis a ON a.item_id = g.item_id AND a.model_name = :model AND a.prompt_version = :pv
        WHERE g.set_name = 'target' AND g.gold_category IS NOT NULL
        ORDER BY g.item_id
    """)
    with eng.connect() as c:
        rows = [dict(r._mapping) for r in c.execute(sql, {"model": model, "pv": pv})]
    n = len(rows)
    if n == 0:
        return {"n": 0, "model": model, "prompt_version": pv, "labeled": False}

    cats = config.CATEGORIES
    sentiments = ("positive", "neutral", "negative")
    # A confusion matrix counts, for every (true label, predicted label) pair, how many times
    # that combination happened. The diagonal (true == predicted) is what the model got right;
    # everything off the diagonal is a specific kind of mistake (e.g. "gold said X, model said Y").
    cat_confusion = {g: dict.fromkeys(cats, 0) for g in cats}
    sent_confusion = {g: dict.fromkeys(sentiments, 0) for g in sentiments}
    cat_correct = sent_correct = 0
    urgency_errors: list[int] = []
    mismatches = []

    for r in rows:
        cat_confusion[r["gold_category"]][r["pred_category"]] += 1
        sent_confusion[r["gold_sentiment"]][r["pred_sentiment"]] += 1
        cat_ok = r["gold_category"] == r["pred_category"]
        sent_ok = r["gold_sentiment"] == r["pred_sentiment"]
        cat_correct += int(cat_ok)
        sent_correct += int(sent_ok)
        err = abs(r["gold_urgency"] - r["pred_urgency"])
        urgency_errors.append(err)
        # Flag this item for manual review if the model got the category wrong, the
        # sentiment wrong, or was off by 2+ urgency levels (a small 0-or-1 urgency miss is
        # normal noise; a bigger miss is worth a human looking at).
        if not cat_ok or not sent_ok or err >= 2:
            mismatches.append({
                "item_id": r["item_id"], "source_type": r["source_type"],
                "gold_category": r["gold_category"], "pred_category": r["pred_category"],
                "gold_sentiment": r["gold_sentiment"], "pred_sentiment": r["pred_sentiment"],
                "gold_urgency": r["gold_urgency"], "pred_urgency": r["pred_urgency"],
                "urgency_error": err, "snippet": r["snippet"], "labeler_notes": r["labeler_notes"],
            })
    mismatches.sort(key=lambda m: m["urgency_error"], reverse=True)

    return {
        "n": n, "model": model, "prompt_version": pv, "labeled": True,
        "category_accuracy": round(cat_correct / n, 3),
        "sentiment_accuracy": round(sent_correct / n, 3),
        # MAE (Mean Absolute Error) here is the average size of the urgency miss, ignoring
        # direction (e.g. predicting 3 when gold says 5, or 5 when gold says 3, both count as 2).
        "urgency_mae": round(sum(urgency_errors) / n, 2),
        "urgency_exact_match_rate": round(sum(1 for e in urgency_errors if e == 0) / n, 3),
        "urgency_within_1_rate": round(sum(1 for e in urgency_errors if e <= 1) / n, 3),
        "categories": list(cats),
        "sentiments": list(sentiments),
        "category_confusion": cat_confusion,
        "sentiment_confusion": sent_confusion,
        "mismatches": mismatches,
    }


def silver_sentiment_report(engine=None, model: str | None = None, pv: str | None = None) -> dict:
    """LLM sentiment vs. the score-derived silver label, across every classified
    item with a star/NPS score — thousands of items, reusing the disagreement
    flag already computed (and stored) by classify/crosscheck.py."""
    eng = engine or create_engine(config.settings.database_url)
    model, pv = _version(model, pv)
    sql = text("""
        SELECT count(*) FILTER (WHERE source_score_disagreement IS NOT NULL) AS n_scored,
               count(*) FILTER (WHERE source_score_disagreement) AS n_disagree
        FROM analysis WHERE model_name = :model AND prompt_version = :pv
    """)
    with eng.connect() as c:
        r = c.execute(sql, {"model": model, "pv": pv}).one()
    n_scored, n_disagree = int(r.n_scored), int(r.n_disagree)
    return {
        "n_scored": n_scored, "n_disagree": n_disagree,
        "accuracy": round(1 - n_disagree / n_scored, 4) if n_scored else None,
    }


def run(model: str | None = None, pv: str | None = None) -> dict:
    """Build both the gold and silver evaluation reports, print them, and return them together as a dict."""
    eng = create_engine(config.settings.database_url)
    gold = gold_report(eng, model, pv)
    silver = silver_sentiment_report(eng, model, pv)
    _print_report(gold, silver)
    return {"gold": gold, "silver_sentiment": silver}


def _fmt_pct(x) -> str:
    return f"{x * 100:.1f}%" if x is not None else "n/a"


def _print_report(gold: dict, silver: dict) -> None:
    """Print a human-readable summary of the gold and silver evaluation reports to the console."""
    print(f"\n=== Gold-set evaluation (n={gold['n']}, {gold['model']}/{gold['prompt_version']}) ===")
    if not gold.get("labeled"):
        print("no labeled gold items found.")
    else:
        print(f"category accuracy   : {_fmt_pct(gold['category_accuracy'])}")
        print(f"sentiment accuracy  : {_fmt_pct(gold['sentiment_accuracy'])}")
        print(f"urgency MAE={gold['urgency_mae']}  exact={_fmt_pct(gold['urgency_exact_match_rate'])}"
              f"  within(+/-1)={_fmt_pct(gold['urgency_within_1_rate'])}")
        print(f"mismatches: {len(gold['mismatches'])}/{gold['n']}")
    print(f"\n=== Silver sentiment accuracy (n={silver['n_scored']} scored items) ===")
    print(f"accuracy vs. star/NPS-derived label: {_fmt_pct(silver['accuracy'])}")
    print("\n(gold labels are human-verified — see gold_candidates.labeler_id; "
          "n=40 is small, so treat per-category rates as directional, not statistically precise)")


if __name__ == "__main__":
    run()
