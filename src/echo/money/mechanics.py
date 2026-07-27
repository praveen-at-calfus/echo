"""The per-category money mechanics, as SQL fragments built from config.

Plain English: this is the rulebook for how a complaint turns into money. Each
category converts differently — a lost shipment risks the whole order value, a
refund complaint exposes the refund amount, a support gripe costs a contact.
This module writes those rules as little SQL snippets so the database does the
arithmetic (the LLM never touches a number).

Technically: helpers that emit SQL ``CASE`` expressions from the config dicts
(``SEVERITY_WEIGHT``, ``SENTIMENT_MULT``, ``CATEGORY_PROPENSITY``, the direct-
exposure knobs). Values come from our own trusted config — plain floats and the
fixed category strings — so they're formatted inline; nothing here is user input.
"""

from __future__ import annotations

from echo import config


def _lit(v) -> str:
    """SQL literal for a trusted config value (float stays bare, string is quoted)."""
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return repr(float(v))


def _case(col: str, mapping: dict, default: str = "0") -> str:
    whens = " ".join(f"WHEN {col}={_lit(k)} THEN {_lit(v)}" for k, v in mapping.items())
    return f"CASE {whens} ELSE {default} END"


def severity_sql(col: str = "urgency") -> str:
    """severity_weight(urgency) — the 1..5 -> {0.1..1.5} curve."""
    return _case(col, config.SEVERITY_WEIGHT, default="0")


def sentiment_sql(col: str = "sentiment") -> str:
    """sentiment_mult — negative 1.0 / neutral 0.4 / positive 0.1."""
    return _case(col, config.SENTIMENT_MULT, default="0")


def propensity_sql(col: str = "category") -> str:
    """category_propensity — how strongly a negative predicts churn (0..1)."""
    return _case(col, config.CATEGORY_PROPENSITY, default="0")


def direct_components(cat: str = "category", ov: str = "order_value",
                      refund: str = "refund_amount", outcome: str = "fulfillment_outcome") -> dict[str, str]:
    """Per-item Direct-Exposure components (deterministic, from real fields).

    Returns name -> SQL expression. Each is 0 unless the item's category (and,
    for lost orders, its fulfillment outcome) matches. The caller sums these and
    applies a ``FILTER (WHERE sentiment='negative')`` so only negatives count.
    """
    lost_outcomes = "('shipped_not_delivered','unavailable','canceled')"
    return {
        # refund pending on a returns/refunds complaint
        "refund_pending": f"CASE WHEN {cat}='Returns & Refunds' THEN COALESCE({refund},0) ELSE 0 END",
        # disputed / double charge -> the refund amount, or the order value as a proxy
        "disputed_charge": f"CASE WHEN {cat}='Billing & Payment' THEN COALESCE({refund},{ov},0) ELSE 0 END",
        # lost / undelivered / canceled shipment risks the whole order value
        "lost_order_value": (f"CASE WHEN {cat}='Shipping & Delivery' AND {outcome} IN {lost_outcomes} "
                             f"THEN COALESCE({ov},0) ELSE 0 END"),
        # defective goods -> reverse-logistics + restocking, a fraction of order value
        "return_cost": (f"CASE WHEN {cat}='Product Quality' "
                        f"THEN {config.RETURN_COST_FRACTION!r}*COALESCE({ov},0) ELSE 0 END"),
        # WISMO / support contact cost on shipping + customer-service issues
        "wismo_cost": (f"CASE WHEN {cat} IN ('Shipping & Delivery','Customer Service') "
                       f"THEN {config.WISMO_CONTACT_COST!r} ELSE 0 END"),
    }
