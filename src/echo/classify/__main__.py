"""`python -m echo.classify` — classify feedback into analysis + llm_calls."""

from __future__ import annotations

import argparse

from echo.classify.runner import run


def main() -> int:
    ap = argparse.ArgumentParser(prog="echo.classify")
    ap.add_argument("--limit", type=int, default=None, help="max texted items (dry-run)")
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args()
    run(limit=args.limit, workers=args.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
