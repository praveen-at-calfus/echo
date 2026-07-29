"""Ticket synthesis (5k, NL-only, no score).

Each ticket is bound to a real order whose facts justify its category; the
generator only phrases facts drawn from ``order_economics``. Category mix,
grounding pools, messy injection, urgency-floor phrasing, timestamps and
near-duplication are all decided deterministically in Python.
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

_TONES = ["educado", "frustrado", "irritado", "confuso", "formal", "urgente"]
_FLOOR = {
    "Billing & Payment": ["fui cobrado em dobro", "parece fraude no meu cartão"],
    "Shipping & Delivery": ["o pedido nunca chegou"],
}


def _allocate(mix: dict[str, float], n: int) -> dict[str, int]:
    """Split n tickets across categories according to the target mix percentages, rounding fairly so the counts add up to exactly n."""
    raw = {c: mix[c] * n for c in mix}
    base = {c: int(v) for c, v in raw.items()}
    rem = n - sum(base.values())
    # Simple truncation (int(v)) usually loses a few tickets to rounding down; hand the
    # leftover ones to whichever categories had the largest fractional remainder, so the
    # total still adds up to exactly n.
    for c, _ in sorted(raw.items(), key=lambda kv: kv[1] - int(kv[1]), reverse=True)[:rem]:
        base[c] += 1
    return base


def _pools(e: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Build, for each ticket category, the subset of orders whose real facts (status, payments, reviews) would justify that category, to ground synthesis on."""
    canceled = e["order_status"].eq("canceled")
    delivered = e["order_status"].eq("delivered")
    return {
        "Shipping & Delivery": e[e["fulfillment_outcome"].isin(["late_delivered", "shipped_not_delivered"])],
        "Billing & Payment": e[canceled | e["payment_mixed"].fillna(False) | e["payment_installments"].ge(6).fillna(False)],
        "Returns & Refunds": e[canceled],
        "Product Quality": e[delivered & e["review_score"].le(2).fillna(False)],
        "Customer Service": e[delivered | e["fulfillment_outcome"].eq("late_delivered")],
        "Availability & Selection": e[e["order_status"].eq("unavailable")],
        "Website/App UX": e[canceled | e["payment_mixed"].fillna(False)],
    }


def _timestamp(rng: np.random.Generator, row, never_arrived: bool):
    """Pick a plausible created_at date for a ticket about this order, falling back to sensible defaults when real dates are missing, and clamp it to the valid date range."""
    purchase = row.purchase_ts
    if pd.isna(purchase):
        purchase = pd.Timestamp(config.DATE_MIN) + timedelta(days=int(rng.integers(0, 700)))
    if never_arrived and not pd.isna(row.estimated_ts):
        # "Never arrived" complaints only make sense after the estimated delivery date has passed.
        created = row.estimated_ts + timedelta(days=int(rng.integers(1, 20)))
    else:
        end = row.delivered_ts if not pd.isna(row.delivered_ts) else row.estimated_ts
        end = end if not pd.isna(end) else purchase + timedelta(days=15)
        if end <= purchase:
            end = purchase + timedelta(days=5)
        # Pick a random point somewhere between purchase and the end date (delivery/estimate).
        created = purchase + (end - purchase) * float(rng.random())
    lo, hi = pd.Timestamp(config.DATE_MIN), pd.Timestamp(config.DATE_MAX)
    created = min(max(created, lo), hi)
    return created.to_pydatetime()


def _jitter(dt, rng: np.random.Generator):
    """Nudge a datetime forward by 0-2 random days (used to space out near-duplicate tickets) without exceeding the max allowed date."""
    out = dt + timedelta(days=int(rng.integers(0, 3)))
    return min(out, config.DATE_MAX)


def _sample_rows(pool: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    """Randomly draw n rows from the grounding pool (reusing rows if the pool is smaller than n) and return them as a fresh DataFrame."""
    rng = np.random.default_rng(seed)
    if len(pool) == 0:
        raise ValueError("empty grounding pool")
    replace = n > len(pool)
    idx = rng.choice(len(pool), size=n, replace=replace)
    return pool.iloc[idx].reset_index(drop=True)


def build_tickets(econ: pd.DataFrame, generator, guard, limit: int | None = None, build_id: str | None = None):
    """Generate the full set of synthetic support tickets grounded on real orders, and return the list of items plus summary stats."""
    n = config.N_TICKETS if limit is None else limit
    alloc = _allocate(config.TICKET_CATEGORY_MIX, n)
    pools = _pools(econ)

    items: list[CorpusItem] = []
    prev: dict[str, list[tuple[str, dict, int]]] = {}
    order_use: Counter = Counter()
    pool_sizes = {c: int(len(pools[c])) for c in alloc}

    for category, count in alloc.items():
        if count == 0:
            continue
        rows = _sample_rows(pools[category], count, utils.child_seed(config.SEED, "ticket-pool", category))
        prev.setdefault(category, [])
        for i, row in enumerate(rows.itertuples(index=False)):
            seed = utils.child_seed(config.SEED, "ticket", category, i)
            rng = np.random.default_rng(seed)
            plan = messy.plan_ticket(rng)

            must_include: list[str] = []
            urgency_floor = False
            never_arrived = category == "Shipping & Delivery" and row.fulfillment_outcome == "shipped_not_delivered"
            # For a fixed fraction of tickets in categories that support it, force in a phrase
            # (e.g. "charged twice", "order never arrived") that guarantees the ticket reads as
            # genuinely urgent, so the corpus has a reliable set of known high-urgency examples.
            if category in _FLOOR and rng.random() < config.TICKET_URGENCY_FLOOR_FRACTION:
                phrase = _FLOOR[category][int(rng.integers(0, len(_FLOOR[category])))]
                must_include.append(phrase)
                urgency_floor = True
                if "nunca chegou" in phrase:
                    never_arrived = True

            tone = "urgente" if urgency_floor else _TONES[int(rng.integers(0, len(_TONES)))]
            brief = {
                "source_type": "ticket",
                "prompt_version": config.PROMPT_VERSION_TICKETS,
                "category": category,
                "seed": seed,
                "style": {"tone": tone, "target_length": plan.target_length, "language": plan.language},
                "content": {
                    "spam": plan.spam, "gibberish": plan.gibberish, "sarcasm": plan.sarcasm,
                    "off_topic": plan.off_topic, "multi_topic": plan.multi_topic, "score_only": False,
                },
                "must_include": must_include,
                "directives": plan.directives,
                "facts": order_facts(row),
            }

            item_id = make_item_id("ticket", f"{row.order_id}:{category}:{i}")
            intended = None if (plan.spam or plan.gibberish) else category

            if plan.near_duplicate and prev[category]:
                # A near-duplicate is a second, slightly-reworded contact about the
                # SAME order: clone the base item (order, flags) and regenerate text.
                base_item, base_brief = prev[category][int(rng.integers(0, len(prev[category])))]
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
                text, rec = _emit_unique(generator, guard, brief, seed)
                guard.add(text)
                item = CorpusItem(
                    item_id=item_id,
                    source_type="ticket",
                    text=text,
                    created_at=_timestamp(rng, row, never_arrived),
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
                    intended_category=intended,
                    provenance=Provenance(
                        synthetic=True,
                        grounded_on=row.order_id,
                        generation_model=rec["generation_model"],
                        prompt_version=config.PROMPT_VERSION_TICKETS,
                        generation_seed=seed,
                        generation_temperature=config.GEN_TEMPERATURE,
                        corpus_build_id=build_id,
                    ),
                    messy=MessyFlags(
                        too_long=len(text) > 1000,  # derived from actual text, not intent
                        spam=plan.spam,
                        gibberish=plan.gibberish,
                        non_target_language=plan.non_target_language,
                        sarcasm=plan.sarcasm,
                        off_topic=plan.off_topic,
                        multi_topic=plan.multi_topic,
                        urgency_floor_signal=urgency_floor,
                    ),
                )
                eff_brief = brief

            items.append(item)
            prev[category].append((item, eff_brief))
            order_use[item.order_id] += 1

    stats = {
        "total": len(items),
        "per_category": dict(Counter(it.intended_category for it in items)),
        "pool_sizes": pool_sizes,
        "distinct_orders": len(order_use),
        "max_reuse": max(order_use.values()) if order_use else 0,
        "urgency_floor": sum(1 for it in items if it.messy.urgency_floor_signal),
        "too_long": sum(1 for it in items if it.messy.too_long),
        "near_dupes": sum(1 for it in items if it.messy.duplicate_of),
        "residual_near_dupes": guard.residual_near_dupes,
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
