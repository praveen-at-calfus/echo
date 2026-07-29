"""Load the built corpus files into PostgreSQL.

    python -m echo.db            # drop + recreate tables, then load
    python -m echo.db --keep     # append into existing tables

Reads ``data/processed/`` (corpus.jsonl, order_economics.parquet,
gold_candidates.jsonl) and writes the tables defined in ``schema.py``. Tables
are truncated/recreated by default so re-loading a rebuilt corpus is idempotent.
Connection comes from ``settings.database_url`` (``DATABASE_URL`` to override).
"""

from __future__ import annotations

import argparse
import math
from datetime import datetime

import pandas as pd
from sqlalchemy import create_engine, insert
from sqlalchemy import text as sqltext

from echo import config
from echo.corpus import utils
from echo.db import schema

_BATCH = 2000

_MESSY_BOOLS = [
    "too_long", "spam", "gibberish", "non_target_language", "sarcasm",
    "off_topic", "multi_topic", "urgency_floor_signal", "condensed",
]


def _clean(v):
    """Coerce pandas/numpy scalars to plain Python; NaN/NaT/NA -> None."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        # pd.isna covers pandas' other "missing" markers (NaT for missing dates, pandas NA),
        # which plain math.isnan above doesn't catch. It can raise on some inputs (e.g. arrays),
        # hence the try/except instead of just calling it directly.
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if hasattr(v, "item"):
        # numpy scalar types (e.g. numpy.int64) aren't plain Python types the DB driver
        # understands; .item() converts them to the equivalent built-in Python type.
        try:
            return v.item()
        except Exception:  # noqa: BLE001
            return v
    return v


def _dt(s):
    """Parse an ISO-format datetime string into a datetime object, or return None if the input is empty/falsy."""
    return datetime.fromisoformat(s) if s else None


def _nonul(v):
    """Strip NUL (0x00) bytes — a rare LLM artifact Postgres text/jsonb reject."""
    return v.replace("\x00", "") if isinstance(v, str) else v


def _feedback_rows():
    """Read corpus.jsonl and yield one dict per feedback item, shaped to match the feedback table's columns."""
    for it in utils.read_jsonl(config.PROCESSED_DIR / "corpus.jsonl"):
        prov = it.get("provenance") or {}
        messy = it.get("messy") or {}
        # provenance and messy are nested objects in the source JSON; here we both flatten
        # their fields into their own top-level columns (so SQL can filter/index on them
        # directly) and keep the whole nested object as JSONB below, for anything that
        # doesn't need its own column.
        row = {
            "item_id": it["item_id"],
            "source_type": it["source_type"],
            "source_score": it.get("source_score"),
            "source_scale": it.get("source_scale"),
            "text": it["text"],
            "created_at": _dt(it.get("created_at")),
            "language": it.get("language"),
            "order_id": it.get("order_id"),
            "customer_id": it.get("customer_id"),
            "customer_unique_id": it.get("customer_unique_id"),
            "order_value": it.get("order_value"),
            "refund_amount": it.get("refund_amount"),
            "freight_value": it.get("freight_value"),
            "payment_type": it.get("payment_type"),
            "product_category_en": it.get("product_category_en"),
            "order_status": it.get("order_status"),
            "fulfillment_outcome": it.get("fulfillment_outcome"),
            "lateness_days": it.get("lateness_days"),
            "silver_label": it.get("silver_label"),
            "silver_label_source": it.get("silver_label_source"),
            "intended_category": it.get("intended_category"),
            "synthetic": prov.get("synthetic"),
            "generation_model": prov.get("generation_model"),
            "prompt_version": prov.get("prompt_version"),
            "corpus_build_id": prov.get("corpus_build_id"),
            "generation_seed": prov.get("generation_seed"),
            "generation_temperature": prov.get("generation_temperature"),
            "duplicate_of": messy.get("duplicate_of"),
            "provenance": prov,
            "messy": messy,
        }
        for f in _MESSY_BOOLS:
            row[f] = messy.get(f)
        yield {k: _nonul(v) for k, v in row.items()}


def _econ_rows():
    """Read the order_economics parquet file and yield one cleaned dict per order row."""
    df = pd.read_parquet(config.ORDER_ECONOMICS_PARQUET)
    for rec in df.to_dict("records"):
        yield {k: _clean(v) for k, v in rec.items()}


def _gold_rows():
    """Read gold_candidates.jsonl and yield one dict per hand-label candidate row, with label columns left blank."""
    for r in utils.read_jsonl(config.PROCESSED_DIR / "gold_candidates.jsonl"):
        gu = str(r.get("gold_urgency") or "").strip()
        yield {
            "item_id": r["item_id"],
            "set_name": r.get("set"),
            "source_type": r.get("source_type"),
            "source_score": str(r.get("source_score") or ""),
            "source_scale": r.get("source_scale") or None,
            "intended_category": r.get("intended_category") or None,
            "product_category_en": r.get("product_category_en") or None,
            "fulfillment_outcome": r.get("fulfillment_outcome") or None,
            "messy_flags": r.get("messy_flags") or None,
            "text": _nonul(r.get("text")),
            "gold_category": r.get("gold_category") or None,
            "gold_sentiment": r.get("gold_sentiment") or None,
            "gold_urgency": int(gu) if gu else None,
            "labeler_notes": r.get("labeler_notes") or None,
            "labeler_id": r.get("labeler_id") or None,
            "labeled_at": None,
        }


def _bulk(conn, table, rows) -> int:
    """Insert an iterable of row dicts into a table in batches of _BATCH rows at a time, returning the total row count inserted."""
    n, batch = 0, []
    for row in rows:
        batch.append(row)
        # Once we've collected a full batch, insert it as one statement (much faster than one
        # insert per row) and reset the batch to start collecting the next one.
        if len(batch) >= _BATCH:
            conn.execute(insert(table), batch)
            n += len(batch)
            batch = []
    if batch:  # insert whatever's left over that didn't fill a full batch
        conn.execute(insert(table), batch)
        n += len(batch)
    return n


_CORPUS_TABLES = "order_economics, feedback, gold_candidates"


def load(recreate: bool = True):
    """Create any missing tables, optionally truncate the corpus tables, then bulk-load order_economics, feedback, and gold_candidates; returns (engine, row-count dict)."""
    engine = create_engine(config.settings.database_url)
    schema.metadata.create_all(engine)  # create any missing tables; never drops
    with engine.begin() as conn:
        if recreate:
            # Clear only the corpus tables. CASCADE also clears any stale rows in
            # dependent pipeline tables (analysis/llm_calls/theme_members) but
            # leaves the pipeline tables themselves — and their schema — intact.
            conn.execute(sqltext(f"TRUNCATE {_CORPUS_TABLES} RESTART IDENTITY CASCADE"))
        ne = _bulk(conn, schema.order_economics, _econ_rows())
        nf = _bulk(conn, schema.feedback, _feedback_rows())
        ng = _bulk(conn, schema.gold_candidates, _gold_rows())
    return engine, {"order_economics": ne, "feedback": nf, "gold_candidates": ng}


_CHECKS = [
    ("rows per source", "SELECT source_type, count(*) FROM feedback GROUP BY 1 ORDER BY 1"),
    ("silver labels", "SELECT coalesce(silver_label,'(none)'), count(*) FROM feedback GROUP BY 1 ORDER BY 2 DESC"),
    ("synthetic vs real", "SELECT synthetic, count(*) FROM feedback GROUP BY 1 ORDER BY 1"),
    ("neg-sentiment $ exposure",
     "SELECT round(sum(order_value)::numeric,2) FROM feedback WHERE silver_label='negative' AND order_value IS NOT NULL"),
    ("orphan order_ids (want 0)",
     "SELECT count(*) FROM feedback f LEFT JOIN order_economics o ON f.order_id=o.order_id "
     "WHERE f.order_id IS NOT NULL AND o.order_id IS NULL"),
]


def main() -> int:
    """Parse command-line arguments, load the corpus into Postgres, and print a verification report; returns the process exit code."""
    ap = argparse.ArgumentParser(prog="echo.db")
    ap.add_argument("--keep", action="store_true", help="append instead of drop + recreate")
    args = ap.parse_args()

    url = config.settings.database_url
    print(f"connecting: {url.rsplit('@', 1)[-1]}")
    engine, counts = load(recreate=not args.keep)
    print(f"loaded: order_economics={counts['order_economics']:,}  "
          f"feedback={counts['feedback']:,}  gold_candidates={counts['gold_candidates']:,}")
    print("\nverification:")
    with engine.connect() as conn:
        for label, q in _CHECKS:
            print(f"  [{label}] {conn.execute(sqltext(q)).fetchall()}")

    print("\ntables in the database:")
    with engine.connect() as conn:
        for t in schema.metadata.sorted_tables:
            n = conn.execute(sqltext(f"SELECT count(*) FROM {t.name}")).scalar()
            note = "" if n else "   (awaiting its pipeline stage)"
            print(f"  {t.name:<16}{n:>8,}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
