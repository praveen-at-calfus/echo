"""Single source of truth for every corpus-build knob.

Mirrors the README principle: *every assumption lives in one documented config*.
Deterministic knobs are module-level constants; environment-driven values
(API key, model, offline switch) live in the ``Settings`` object.
"""

from __future__ import annotations

import getpass
from datetime import datetime
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"            # the 9 downloaded Olist CSVs (inputs)
PROCESSED_DIR = DATA_DIR / "processed"  # build outputs (corpus, parquet, gold, manifest)
INTERIM_DIR = DATA_DIR / "interim"      # scratch (the run-once LLM generation cache)
CACHE_DIR = INTERIM_DIR / "generation_cache"

RAW_FILES = {
    "orders": "olist_orders_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "customers": "olist_customers_dataset.csv",
    "products": "olist_products_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}

ORDER_ECONOMICS_PARQUET = PROCESSED_DIR / "order_economics.parquet"
ORDER_ECONOMICS_STATS = PROCESSED_DIR / "order_economics_stats.json"

# --------------------------------------------------------------------------- #
# Reproducibility
# --------------------------------------------------------------------------- #
SEED = 20260724
BUILDER_VERSION = "0.1.0"

# --------------------------------------------------------------------------- #
# Taxonomy — the fixed 10 owner-aligned routing categories (single-label).
# --------------------------------------------------------------------------- #
CATEGORIES: tuple[str, ...] = (
    "Product Quality",
    "Shipping & Delivery",
    "Returns & Refunds",
    "Billing & Payment",
    "Pricing & Value",
    "Website/App UX",
    "Customer Service",
    "Availability & Selection",
    "Praise",
    "Other/Unclear",
)

# --------------------------------------------------------------------------- #
# Corpus sizes
# --------------------------------------------------------------------------- #
N_REVIEWS = 5000
N_TICKETS = 5000
N_SURVEYS = 5000

# --------------------------------------------------------------------------- #
# Ticket synthesis — category mix (must sum to ~1.0). Tickets skew heavily to
# shipping/billing/returns; ~no praise (nobody opens a ticket to say thanks).
# --------------------------------------------------------------------------- #
TICKET_CATEGORY_MIX: dict[str, float] = {
    "Shipping & Delivery": 0.38,
    "Billing & Payment": 0.20,
    "Returns & Refunds": 0.16,
    "Product Quality": 0.12,
    "Customer Service": 0.08,
    "Availability & Selection": 0.04,
    "Website/App UX": 0.02,
}

# --------------------------------------------------------------------------- #
# Messy-input injection fractions (Bernoulli per item; realized counts reported
# in the manifest). Tickets carry the variety real Olist reviews lack (which are
# uniformly short, <=208 chars). Surveys get a lighter touch.
# --------------------------------------------------------------------------- #
MESSY_FRACTIONS_TICKETS: dict[str, float] = {
    "long": 0.06,        # target 1000-2000 chars -> triggers downstream condense
    "very_long": 0.02,   # target >2000 chars
    "spam": 0.01,
    "gibberish": 0.01,
    "non_target_language": 0.03,
    "near_duplicate": 0.03,
    "sarcasm": 0.03,
    "off_topic": 0.02,
    "multi_topic": 0.05,
}
# Fraction of billing/shipping tickets that carry explicit urgency-floor phrasing
# ("fraude", "cobrança em dobro", "nunca chegou") -> urgency_floor_signal=True.
TICKET_URGENCY_FLOOR_FRACTION = 0.10

MESSY_FRACTIONS_SURVEYS: dict[str, float] = {
    "non_target_language": 0.02,
    "near_duplicate": 0.02,
    "score_only": 0.05,  # empty text -> exercises the score-only path
}

# --------------------------------------------------------------------------- #
# MCMC review sampler
# --------------------------------------------------------------------------- #
# Target for the joint distribution over (score x category-group x length-band).
#   "representative" -> match the empirical population joint (honest / realistic).
#   "balanced"       -> flatten across score bands.
# The value is a knob; representative is the locked default.
MCMC_TARGET = "representative"
MCMC_ITERS = 120_000
MCMC_T0 = 1.0            # initial temperature
MCMC_T_MIN = 0.02        # final temperature (geometric anneal)
TEXT_LENGTH_BANDS = (50, 120)  # short <50, med 50-120, long >120 (chars)

# --------------------------------------------------------------------------- #
# NPS (surveys) -> sentiment thresholds and star->NPS coherence bands
# --------------------------------------------------------------------------- #
NPS_NEG_MAX = 4          # 0-4  -> negative
NPS_POS_MIN = 7          # 7-10 -> positive (5-6 -> None / passive)
STAR_TO_NPS_BAND: dict[int, tuple[int, int]] = {
    5: (9, 10),
    4: (7, 8),
    3: (5, 6),
    2: (3, 4),
    1: (0, 2),
}

# --------------------------------------------------------------------------- #
# Prompt versions (bump to produce a new corpus build without mutating the old)
# --------------------------------------------------------------------------- #
PROMPT_VERSION_TICKETS = "tickets_v1"
PROMPT_VERSION_SURVEYS = "surveys_v1"

# --------------------------------------------------------------------------- #
# Date window (all items must fall inside the real Olist activity span)
# --------------------------------------------------------------------------- #
DATE_MIN = datetime(2016, 9, 1)
DATE_MAX = datetime(2018, 11, 1)

# --------------------------------------------------------------------------- #
# Gold set
# --------------------------------------------------------------------------- #
GOLD_TARGET = 40
GOLD_RESERVE = 20

# --------------------------------------------------------------------------- #
# Generation defaults
# --------------------------------------------------------------------------- #
GEN_TEMPERATURE = 0.8
GEN_WORKERS = 8  # concurrent LLM calls when warming the cache


class Settings(BaseSettings):
    """Environment-driven settings (``.env`` + real env vars)."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    model: str = Field(default="gpt-4o-mini", alias="ECHO_MODEL")
    offline: bool = Field(default=False, alias="ECHO_OFFLINE")
    database_url: str = Field(
        default_factory=lambda: f"postgresql+psycopg://{getpass.getuser()}@localhost:5432/echo",
        alias="DATABASE_URL",
    )

    @property
    def use_offline(self) -> bool:
        """Offline stub generation when explicitly requested or no key present."""
        return self.offline or not self.openai_api_key


settings = Settings()
