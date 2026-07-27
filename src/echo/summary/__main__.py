"""``python -m echo.summary`` — generate the weekly briefing for one week."""

from __future__ import annotations

import argparse

from echo import config
from echo.summary import run


def main() -> int:
    ap = argparse.ArgumentParser(prog="echo.summary")
    ap.add_argument("--week", default=config.DEMO_WEEK, help=f"ISO week start (default DEMO_WEEK {config.DEMO_WEEK})")
    args = ap.parse_args()
    run(week=args.week)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
