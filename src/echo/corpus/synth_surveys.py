"""Survey synthesis (5k, NPS 0-10 + text).

Grounded on real customers/orders (prefer delivered = post-purchase). The NPS
score is chosen in Python — coherent with the order's real review score where
present, else derived from the fulfillment outcome — and a deliberate ~30% is
drawn from problem orders so surveys carry the full sentiment range reviews
lack. The LLM only writes text matching the target sentiment.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

import numpy as np
import pandas as pd

from echo import config
from echo.corpus import messy, utils
from echo.corpus.synth_common import order_facts
from echo.schemas.envelope import CorpusItem, MessyFlags, Provenance, make_item_id

_PROBLEM_FRACTION = 0.30


def _sentiment(nps: int) -> str:
    """Classify an NPS score (0-10) into detractor, passive, or promoter."""
    return "detractor" if nps <= config.NPS_NEG_MAX else ("passive" if nps <= 6 else "promoter")


def _silver(nps: int):
    """Derive a coarse negative/positive silver label from an NPS score, or None if it falls in the neutral middle band."""
    if nps <= config.NPS_NEG_MAX:
        return "negative"
    if nps >= config.NPS_POS_MIN:
        return "positive"
    return None


def _nps(rng: np.random.Generator, row) -> int:
    """Pick a plausible NPS score (0-10) for this order: reuse the real review's star rating band if one exists, otherwise infer a range from the fulfillment outcome, otherwise fall back to a generally-positive random default."""
    if not pd.isna(row.review_score):
        lo, hi = config.STAR_TO_NPS_BAND[int(row.review_score)]
        return int(rng.integers(lo, hi + 1))
    fo = row.fulfillment_outcome
    if fo in ("canceled", "unavailable", "shipped_not_delivered"):
        return int(rng.integers(0, 5))
    if fo == "late_delivered":
        return int(rng.integers(3, 7))
    # No review and no known problem: skew toward a good score, with a smaller chance of a
    # middling or lower one, so surveys aren't unrealistically uniform.
    r = rng.random()
    return int(rng.integers(9, 11)) if r < 0.6 else (int(rng.integers(7, 9)) if r < 0.85 else int(rng.integers(5, 7)))


def _timestamp(rng: np.random.Generator, row):
    """Pick a plausible created_at date for a survey response, shortly after delivery (or purchase if not delivered), clamped to the valid date range."""
    base = row.delivered_ts if not pd.isna(row.delivered_ts) else row.purchase_ts
    if pd.isna(base):
        base = pd.Timestamp(config.DATE_MIN) + timedelta(days=int(rng.integers(0, 700)))
    created = base + timedelta(days=int(rng.integers(1, 14)))
    lo, hi = pd.Timestamp(config.DATE_MIN), pd.Timestamp(config.DATE_MAX)
    return min(max(created, lo), hi).to_pydatetime()


def _jitter(dt, rng: np.random.Generator):
    """Nudge a datetime forward by 0-2 random days (used to space out near-duplicate survey responses) without exceeding the max allowed date."""
    out = dt + timedelta(days=int(rng.integers(0, 3)))
    return min(out, config.DATE_MAX)


def _sample_orders(econ: pd.DataFrame, n: int) -> pd.DataFrame:
    """Pick n orders to ground surveys on: about 30% deliberately from orders with problems (late/canceled/unavailable/low review score) so surveys cover the full sentiment range, the rest from general delivered orders, then shuffle the result."""
    rng = np.random.default_rng(utils.child_seed(config.SEED, "survey-pool"))
    problem = econ[
        econ["fulfillment_outcome"].isin(["late_delivered", "canceled", "unavailable", "shipped_not_delivered"])
        | econ["review_score"].le(3).fillna(False)
    ]
    general = econ[econ["order_status"].eq("delivered")]

    n_problem = min(int(round(_PROBLEM_FRACTION * n)), len(problem))
    n_general = n - n_problem

    def take(pool, k):
        """Randomly draw k rows from a pool, reusing rows if the pool is smaller than k."""
        return pool.iloc[rng.choice(len(pool), size=k, replace=k > len(pool))]

    rows = pd.concat([take(problem, n_problem), take(general, n_general)])
    return rows.sample(frac=1, random_state=config.SEED).reset_index(drop=True)


def build_surveys(econ: pd.DataFrame, generator, guard, limit: int | None = None, build_id: str | None = None):
    """Generate the full set of synthetic post-purchase surveys grounded on real orders, and return the list of items plus summary stats."""
    n = config.N_SURVEYS if limit is None else limit
    rows = _sample_orders(econ, n)

    items: list[CorpusItem] = []
    prev: list = []
    order_use: Counter = Counter()

    for i, row in enumerate(rows.itertuples(index=False)):
        seed = utils.child_seed(config.SEED, "survey", i)
        rng = np.random.default_rng(seed)
        plan = messy.plan_survey(rng)
        item_id = make_item_id("survey", f"{row.order_id}:{i}")

        if plan.near_duplicate and prev:
            # Near-duplicate = second response about the SAME order: clone base
            # (score, silver, flags, order) and regenerate near-identical text.
            base_item, base_brief = prev[int(rng.integers(0, len(prev)))]
            rec = generator.generate({**base_brief, "seed": seed}, seed)
            item = base_item.model_copy(
                update={
                    "item_id": item_id,
                    "text": rec["text"],
                    "created_at": _jitter(base_item.created_at, rng),
                    "messy": base_item.messy.model_copy(update={"duplicate_of": base_item.item_id}),
                    "provenance": base_item.provenance.model_copy(
                        update={"generation_seed": seed, "corpus_build_id": build_id}
                    ),
                }
            )
            eff_brief = base_brief
        else:
            nps = _nps(rng, row)
            sentiment = _sentiment(nps)
            brief = {
                "source_type": "survey",
                "prompt_version": config.PROMPT_VERSION_SURVEYS,
                "target_sentiment": sentiment,
                "seed": seed,
                "style": {"tone": sentiment, "target_length": plan.target_length, "language": plan.language},
                "content": {"score_only": plan.score_only, "spam": False, "gibberish": False,
                            "sarcasm": False, "off_topic": False, "multi_topic": False},
                "directives": plan.directives,
                "facts": order_facts(row),
            }
            if plan.score_only:
                text, gen_model = "", "score-only"
            else:
                text, rec = _emit_unique(generator, guard, brief, seed)
                gen_model = rec["generation_model"]
                guard.add(text)
            item = CorpusItem(
                item_id=item_id,
                source_type="survey",
                source_score=float(nps),
                source_scale="nps_0_10",
                text=text,
                created_at=_timestamp(rng, row),
                language=plan.language,
                order_id=row.order_id,
                customer_id=_s(row.customer_id),
                customer_unique_id=_s(row.customer_unique_id),
                order_value=_f(row.order_value),
                refund_amount=_f(row.refund_amount_proxy),
                freight_value=_f(row.freight_value),
                payment_type=_s(row.payment_type),
                product_category_en=_s(row.product_category_en),
                order_status=_s(row.order_status),
                fulfillment_outcome=_s(row.fulfillment_outcome),
                lateness_days=_f(row.lateness_days),
                silver_label=_silver(nps),
                silver_label_source="survey_nps" if _silver(nps) else None,
                provenance=Provenance(
                    synthetic=True,
                    grounded_on=row.order_id,
                    generation_model=gen_model,
                    prompt_version=config.PROMPT_VERSION_SURVEYS,
                    generation_seed=seed,
                    generation_temperature=config.GEN_TEMPERATURE,
                    corpus_build_id=build_id,
                ),
                messy=MessyFlags(
                    too_short=plan.score_only,
                    non_target_language=plan.non_target_language,
                ),
            )
            eff_brief = brief

        items.append(item)
        prev.append((item, eff_brief))
        order_use[item.order_id] += 1

    stats = {
        "total": len(items),
        "silver": dict(Counter(it.silver_label for it in items)),
        "sentiment": dict(Counter(_sentiment(int(it.source_score)) for it in items)),
        "nps_mean": round(float(np.mean([it.source_score for it in items])), 2),
        "detractors": sum(1 for it in items if it.source_score <= config.NPS_NEG_MAX),
        "score_only": sum(1 for it in items if it.messy.too_short),
        "non_pt": sum(1 for it in items if it.messy.non_target_language),
        "near_dupes": sum(1 for it in items if it.messy.duplicate_of),
        "distinct_orders": len(order_use),
    }
    return items, stats


def _emit_unique(generator, guard, brief, seed):
    """Generate text for this brief, retrying up to 3 times with different sub-seeds if the diversity guard flags an unintended duplicate, and return the text with its generation record."""
    for attempt in range(3):
        s = seed if attempt == 0 else utils.child_seed(seed, "retry", attempt)
        rec = generator.generate({**brief, "seed": s}, s)
        if guard.check(rec["text"]):
            return rec["text"], rec
    guard.note_residual()
    return rec["text"], rec


def _f(v):
    """Convert a value to a plain float, or return None if it is missing/NaN."""
    return None if v is None or pd.isna(v) else float(v)


def _s(v):
    """Convert a value to a plain string, or return None if it is missing/NaN."""
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
