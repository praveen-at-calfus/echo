"""``python -m echo.api`` — run the API with uvicorn."""

from __future__ import annotations

import argparse


def main() -> int:
    ap = argparse.ArgumentParser(prog="echo.api")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()

    import uvicorn
    uvicorn.run("echo.api.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
