"""``python -m echo.embed`` — embed texted feedback into the ``embeddings`` table.

``--verify`` skips embedding and prints cosine nearest-neighbours for a few items
(the stage's ✅ check: semantically-close items with no shared keywords).
"""

from __future__ import annotations

import argparse

from sqlalchemy import create_engine, func, select

from echo import config
from echo.db import schema, vector
from echo.embed import nearest, run


def _verify(n: int = 3, k: int = 4) -> None:
    """Print a handful of sample feedback items alongside their nearest semantic neighbours, as a quick sanity check that similar-meaning items really do end up close together."""
    engine = create_engine(config.settings.database_url)
    e, f = vector.embeddings, schema.feedback
    with engine.connect() as c:
        seeds = c.execute(
            select(f.c.item_id, f.c.text)
            .select_from(f.join(e, e.c.item_id == f.c.item_id))
            .where(func.length(f.c.text) > 40).order_by(f.c.item_id).limit(n)
        ).all()
    for s in seeds:
        print(f"\nseed [{s.item_id[:8]}] {s.text[:90]!r}")
        for nb in nearest(engine, s.item_id, k=k):
            print(f"  d={nb['distance']:.3f} [{nb['source_type']}] {nb['snippet']!r}")


def main() -> int:
    """Parse command-line options and either run the embedding batch job or print the neighbour-verification sample."""
    ap = argparse.ArgumentParser(prog="echo.embed")
    ap.add_argument("--limit", type=int, default=None, help="max texted items (dry-run)")
    ap.add_argument("--verify", action="store_true", help="print cosine neighbours instead of embedding")
    args = ap.parse_args()
    if args.verify:
        _verify()
    else:
        run(limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
