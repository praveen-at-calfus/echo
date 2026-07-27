"""Offline corpus-builder / data-prep step for echo.

Produces the reproducible 15k-item feedback corpus (5k sampled real reviews +
5k synthesized tickets + 5k synthesized surveys) in the common envelope, plus
the order-economics reference view and gold/silver scaffolding. Decoupled from
Postgres/Milvus — writes files under ``data/processed/``.
"""
