"""``python -m echo.money`` — print the money report (per-category exposure + retention).

Plain English: a quick, readable dump of what the money engine computes — the
tier the data supports, per-category Direct Exposure with its breakdown, and the
modeled Retention Risk range. Nothing is written; this is the stage's ✅ check.
"""

from __future__ import annotations

import argparse

from echo import config
from echo.money import engine


def _fmt(n: float) -> str:
    return f"R${n:,.0f}"


def main() -> int:
    ap = argparse.ArgumentParser(prog="echo.money")
    ap.add_argument("--week", default=None, help="ISO week start YYYY-MM-DD (default: all-time)")
    ap.add_argument("--demo-week", action="store_true", help=f"use DEMO_WEEK ({config.DEMO_WEEK})")
    args = ap.parse_args()
    week = config.DEMO_WEEK if args.demo_week else args.week

    rep = engine.summary(week=week)
    t = rep["totals"]
    print(f"\nMoney report · window={rep['week']} · coverage tier={rep['tier']} · {rep['currency']}")
    print(f"  {t['items']:,} items · {t['negatives']:,} negative · "
          f"Direct Exposure {_fmt(t['direct_exposure'])} · "
          f"Retention Risk (modeled) {_fmt(t['retention']['low'])}–{_fmt(t['retention']['high'])} "
          f"(base {_fmt(t['retention']['base'])}, {t['at_risk_customers']:,} at-risk customers)\n")

    hdr = f"  {'category':<26}{'neg':>6}{'impact':>12}{'direct $':>14}{'retention base':>18}   owner"
    print(hdr)
    print("  " + "-" * (len(hdr) + 6))
    for c in rep["categories"]:
        r = c["retention"]
        print(f"  {c['category']:<26}{c['n_neg']:>6}{c['impact']:>12,.0f}"
              f"{_fmt(c['direct_exposure']):>14}{_fmt(r['base']):>18}   {c['owner']}")
    print("\n  Direct Exposure = deterministic, from real fields (refund / disputed charge / "
          "lost-order / return cost / WISMO).")
    print("  Retention Risk  = MODELED estimate (customer_value × churn_uplift × category_propensity); "
          "range shown, not a point.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
