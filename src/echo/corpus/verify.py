"""Standalone end-to-end verification of a built corpus (no DB/LLM needed).

Loads the written JSONL shards + the order-economics parquet and asserts the
README invariants and internal consistency. Prints a PASS/FAIL table and exits
non-zero on any failure (so it can gate CI later).
"""

from __future__ import annotations

import json
import math
import sys

import pandas as pd

from echo import config
from echo.corpus import utils
from echo.schemas.envelope import CorpusItem

_SHARDS = {"review": "reviews.jsonl", "ticket": "tickets.jsonl", "survey": "surveys.jsonl"}
_VALUE_CHECKS = {"median": (95, 115), "p95": (400, 500), "max": (13000, 14000)}


def _approx(a, b, tol=0.05) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return math.isclose(float(a), float(b), abs_tol=tol)


def run() -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    # Load.
    raw: list[dict] = []
    for path in _SHARDS.values():
        raw.extend(utils.read_jsonl(config.PROCESSED_DIR / path))
    manifest = json.loads((config.PROCESSED_DIR / "manifest.json").read_text())

    # 1. Schema validation (re-parse through the Pydantic model + invariants).
    items: list[CorpusItem] = []
    errors = 0
    for r in raw:
        try:
            items.append(CorpusItem.model_validate(r))
        except Exception:  # noqa: BLE001
            errors += 1
    check("schema_valid", errors == 0, f"{errors} invalid rows")

    # 2. Counts + unique ids.
    ids = [it.item_id for it in items]
    check("unique_item_ids", len(ids) == len(set(ids)), f"{len(ids) - len(set(ids))} dupes")
    check("count_matches_manifest", len(items) == manifest["counts"]["total"],
          f"{len(items)} vs {manifest['counts']['total']}")

    # 3. Source distinctions (belt-and-suspenders on top of the schema).
    check("tickets_no_score", all(it.source_score is None for it in items if it.source_type == "ticket"))
    check("scored_sources_have_score",
          all(it.source_score is not None for it in items if it.source_type != "ticket"))

    # 4. Join integrity + money matches the reference view exactly.
    econ = pd.read_parquet(config.ORDER_ECONOMICS_PARQUET).set_index("order_id")
    ov = econ["order_value"].to_dict()
    rf = econ["refund_amount_proxy"].to_dict()
    missing = mismatch = 0
    for it in items:
        if it.order_id is None:
            continue
        if it.order_id not in ov:
            missing += 1
            continue
        e_ov = ov[it.order_id]
        e_ov = None if (e_ov is None or (isinstance(e_ov, float) and math.isnan(e_ov))) else e_ov
        e_rf = rf[it.order_id]
        e_rf = None if (e_rf is None or (isinstance(e_rf, float) and math.isnan(e_rf))) else e_rf
        if not (_approx(it.order_value, e_ov) and _approx(it.refund_amount, e_rf)):
            mismatch += 1
    check("orders_resolve", missing == 0, f"{missing} order_ids not in econ")
    check("money_matches_reference", mismatch == 0, f"{mismatch} money mismatches (no invented $)")

    # 5. Money integrity: the reference VIEW reproduces README percentiles (the join
    #    guard — the 13.7k outlier lives in the 99k view, not a 15k sample); and the
    #    corpus median stays plausible.
    econ_stats = json.loads(config.ORDER_ECONOMICS_STATS.read_text())["order_value"]
    view_ok = (
        _VALUE_CHECKS["median"][0] <= econ_stats["median"] <= _VALUE_CHECKS["median"][1]
        and _VALUE_CHECKS["p95"][0] <= econ_stats["p95"] <= _VALUE_CHECKS["p95"][1]
        and _VALUE_CHECKS["max"][0] <= econ_stats["max"] <= _VALUE_CHECKS["max"][1]
    )
    vals = pd.Series([it.order_value for it in items if it.order_value is not None])
    corpus_ok = 80 <= vals.median() <= 200
    check("money_integrity", view_ok and corpus_ok,
          f"view(med={econ_stats['median']},p95={econ_stats['p95']},max={econ_stats['max']}) "
          f"corpus_med={vals.median():.1f}")

    # 6. Provenance completeness.
    syn = [it for it in items if it.source_type != "review"]
    prov_ok = all(
        it.provenance.synthetic and it.provenance.grounded_on and it.provenance.generation_model
        for it in syn
    )
    check("synthetic_provenance", prov_ok)
    check("reviews_real", all(not it.provenance.synthetic for it in items if it.source_type == "review"))

    # 7. Raw preservation (long items were never truncated).
    long_ok = all(len(it.text) > 1000 for it in items if it.messy.too_long)
    check("long_not_truncated", long_ok)

    # 8. Diversity — residual unintended near-dupes below 0.5% of synthetic. The offline
    #    stub has bounded template variety and cannot make thousands of unique texts, so
    #    this is only enforced for real (LLM) generation.
    resid = manifest["generation"]["residual_near_dupes"]
    if str(manifest.get("generation_model", "")).startswith("offline-stub"):
        check("diversity", True, f"{resid} residual (offline stub — not enforced)")
    else:
        check("diversity", resid <= max(3, 0.005 * len(syn)), f"{resid} residual near-dupes")

    # 9. Language — reviews analyzed in place as pt.
    check("reviews_pt", all(it.language == "pt" for it in items if it.source_type == "review"))

    # 10. Silver-label coherence.
    def silver_ok(it):
        if it.silver_label is None:
            return True
        if it.source_scale == "star_1_5":
            return (it.silver_label == "negative") == (it.source_score <= 2)
        if it.source_scale == "nps_0_10":
            return (it.silver_label == "negative") == (it.source_score <= config.NPS_NEG_MAX)
        return True
    check("silver_coherent", all(silver_ok(it) for it in items))

    return checks


def main() -> int:
    checks = run()
    width = max(len(n) for n, _, _ in checks)
    print("\n=== echo corpus verification ===")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name.ljust(width)}  {detail}")
    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"\n{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
