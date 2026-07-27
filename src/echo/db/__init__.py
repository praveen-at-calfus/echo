"""PostgreSQL persistence for echo (SQLAlchemy Core).

The offline corpus-builder writes files under ``data/processed/``; this package
loads them into Postgres so the live pipeline (and the SQL money engine) can
query them. Tables mirror the README's data stores: ``feedback`` (immutable raw
items) + the ``order_economics`` reference view, plus ``gold_candidates``.
"""
