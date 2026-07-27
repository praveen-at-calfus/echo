"""Entry point for `python -m echo.db` — load the built corpus into PostgreSQL.

This is just the launcher; the real work (schema + bulk insert) lives in load.py.
"""

from echo.db.load import main

if __name__ == "__main__":
    raise SystemExit(main())
