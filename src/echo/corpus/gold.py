"""Gold-set candidate export (SELECT & EXPORT only — never auto-label).

The builder stratifies a candidate set across sources, score/sentiment bands,
categories and boundary/edge cases, and exports it with **blank** label columns
for a human to fill. It never assigns gold categories (silver labels from scores
are separate and allowed). ~40 target + 20 reserve so the labeler can discard
ambiguous items and still land 40.
"""

from __future__ import annotations

import csv
import random

from echo import config
from echo.corpus import utils
from echo.schemas.envelope import CorpusItem

_CSV_COLUMNS = [
    "set", "item_id", "source_type", "source_score", "source_scale",
    "intended_category", "product_category_en", "fulfillment_outcome",
    "messy_flags", "text",
    # --- blank columns for the human labeler ---
    "gold_category", "gold_sentiment", "gold_urgency", "labeler_notes",
    "labeler_id", "labeled_at",
]


def _messy_summary(it: CorpusItem) -> str:
    """Build a short comma-separated string listing which messy flags (spam, sarcasm, duplicate, etc.) are set on this item."""
    flags = [k for k, v in it.messy.model_dump().items() if v is True]
    if it.messy.duplicate_of:
        flags.append("duplicate")
    return ",".join(flags)


def _is_edge(it: CorpusItem) -> bool:
    """Return True if this item has any messy trait that makes it a tricky edge case worth prioritizing for labeling."""
    m = it.messy
    return any([m.sarcasm, m.off_topic, m.multi_topic, m.spam, m.gibberish,
                m.non_target_language, m.urgency_floor_signal])


def _review_stratum(it: CorpusItem) -> str:
    """Return the stratification bucket name for a review item, based on its star score."""
    return f"review_score{int(it.source_score)}"


def _survey_stratum(it: CorpusItem) -> str:
    """Return the stratification bucket name for a survey item, based on whether it's score-only or its NPS band (detractor/passive/promoter)."""
    if it.messy.too_short:
        return "survey_score_only"
    s = int(it.source_score)
    return "survey_detractor" if s <= 4 else ("survey_passive" if s <= 6 else "survey_promoter")


def _ticket_stratum(it: CorpusItem) -> str:
    """Return the stratification bucket name for a ticket item, based on its intended category (or 'edge' if none)."""
    return f"ticket_{it.intended_category or 'edge'}"


def _stratify(items: list[CorpusItem], rng: random.Random) -> dict[str, list[CorpusItem]]:
    """Group items into stratification buckets by source type and score/category, sorting each bucket so tricky edge-case items come first."""
    buckets: dict[str, list[CorpusItem]] = {}
    for it in items:
        if it.source_type == "review":
            key = _review_stratum(it)
        elif it.source_type == "survey":
            key = _survey_stratum(it)
        else:
            key = _ticket_stratum(it)
        buckets.setdefault(key, []).append(it)
    # Edge/boundary items float to the front of each bucket (over-sampled).
    for key in buckets:
        buckets[key].sort(key=lambda it: (not _is_edge(it), it.item_id))
    return buckets


def build_gold_candidates(items: list[CorpusItem]) -> list[dict]:
    """Pick a stratified, source-balanced sample of candidate items (target + reserve) for human gold labeling, and return them as export-ready row dicts with blank label columns."""
    rng = random.Random(utils.child_seed(config.SEED, "gold"))
    total = config.GOLD_TARGET + config.GOLD_RESERVE  # 60
    buckets = _stratify(items, rng)
    keys = sorted(buckets)

    # Round-robin across strata for an even, source-balanced spread.
    picked: list[CorpusItem] = []
    cursors = {k: 0 for k in keys}
    while len(picked) < total and any(cursors[k] < len(buckets[k]) for k in keys):
        for k in keys:
            if cursors[k] < len(buckets[k]):
                picked.append(buckets[k][cursors[k]])
                cursors[k] += 1
                if len(picked) >= total:
                    break

    rows = []
    for rank, it in enumerate(picked):
        rows.append(
            {
                "set": "target" if rank < config.GOLD_TARGET else "reserve",
                "item_id": it.item_id,
                "source_type": it.source_type,
                "source_score": "" if it.source_score is None else it.source_score,
                "source_scale": it.source_scale or "",
                "intended_category": it.intended_category or "",
                "product_category_en": it.product_category_en or "",
                "fulfillment_outcome": it.fulfillment_outcome or "",
                "messy_flags": _messy_summary(it),
                "text": it.text,
                "gold_category": "",
                "gold_sentiment": "",
                "gold_urgency": "",
                "labeler_notes": "",
                "labeler_id": "",
                "labeled_at": "",
            }
        )
    return rows


def write_gold(items: list[CorpusItem]) -> dict:
    """Build the gold candidate rows and write them to CSV, JSONL, and a labeling-instructions file, then return summary stats about the export."""
    import json

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    rows = build_gold_candidates(items)

    csv_path = config.PROCESSED_DIR / "gold_candidates.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)

    jsonl_path = config.PROCESSED_DIR / "gold_candidates.jsonl"
    jsonl_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )

    (config.PROCESSED_DIR / "gold_instructions.md").write_text(_INSTRUCTIONS, encoding="utf-8")
    return {
        "candidates": len(rows),
        "target": config.GOLD_TARGET,
        "reserve": config.GOLD_RESERVE,
        "edge_cases": sum(1 for r in rows if r["messy_flags"]),
        "by_source": {
            s: sum(1 for r in rows if r["source_type"] == s) for s in ("review", "ticket", "survey")
        },
    }


_INSTRUCTIONS = """# Gold-set labeling instructions

Fill the blank columns (`gold_category`, `gold_sentiment`, `gold_urgency`,
`labeler_notes`, `labeler_id`, `labeled_at`) in `gold_candidates.csv`. Label the
`target` rows first; use `reserve` rows only to replace any you discard as too
ambiguous, keeping 40 final labels. Do NOT trust `intended_category` — it is a
synthesis hint for synthetic items, not ground truth.

## gold_category — pick exactly ONE:
Product Quality · Shipping & Delivery · Returns & Refunds · Billing & Payment ·
Pricing & Value · Website/App UX · Customer Service · Availability & Selection ·
Praise · Other/Unclear

## Boundary rules
- Damaged in transit -> Shipping; defective / not-as-described -> Product Quality.
- Returns & Refunds only when the *process* itself is the complaint.
- Money/charge problem -> Billing; checkout *page/flow* error -> Website/App UX.
- "Too expensive / competitor cheaper" -> Pricing (not Billing).
- Out-of-stock / "wish you sold X" -> Availability.
- Wrong item received -> Shipping.
- Praise + complaint -> categorize by the complaint. Login/account -> UX.
- Off-topic / sarcasm / gibberish with no clear issue -> Other/Unclear.

## gold_sentiment: positive | neutral | negative  (of the categorized aspect)
## gold_urgency: 1-5
5 fraud / payment taken no order / safety / mass outage · 4 individual money at
stake or purchase blocked · 3 delayed order needing follow-up · 2 minor
dissatisfaction · 1 praise / no action.

Text is Brazilian Portuguese — label from the Portuguese.
"""
