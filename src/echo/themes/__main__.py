"""``python -m echo.themes`` — build weekly themes.

Default: the demo week plus a few preceding weeks (so the weekly summary's trend
line has real themes). Use ``--week`` for a single week, ``--trend N`` to change
how many preceding weeks are included, ``--threshold`` to tune the cluster cut.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from echo import config
from echo.themes import run


def _weeks(anchor: str, trend: int) -> list[str]:
    """Build a sorted list of ISO week-start dates: the anchor week plus the given number of preceding weeks."""
    start = datetime.strptime(anchor, "%Y-%m-%d")
    weeks = [(start - timedelta(days=7 * i)).strftime("%Y-%m-%d") for i in range(trend + 1)]
    return sorted(weeks)


def main() -> int:
    """Parse command-line arguments and run the themes pipeline for one or more weeks, printing a combined total."""
    ap = argparse.ArgumentParser(prog="echo.themes")
    ap.add_argument("--week", default=None, help="a single ISO week start (overrides --trend)")
    ap.add_argument("--trend", type=int, default=4, help="preceding weeks to include (default 4)")
    ap.add_argument("--threshold", type=float, default=None, help="override cluster distance cut")
    args = ap.parse_args()

    weeks = [args.week] if args.week else _weeks(config.DEMO_WEEK, args.trend)
    grand = {"themes": 0, "est_cost": 0.0}
    for wk in weeks:
        r = run(week=wk, threshold=args.threshold)
        grand["themes"] += r.get("themes", 0)
        grand["est_cost"] += r.get("est_cost", 0.0)
    if len(weeks) > 1:
        print(f"TOTAL: {grand['themes']} themes across {len(weeks)} weeks · est. ${grand['est_cost']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
