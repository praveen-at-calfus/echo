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

# --------------------------------------------------------------------------- #
# Classify stage (real-time per item: category + sentiment + urgency + rationale)
# --------------------------------------------------------------------------- #
CLASSIFY_PROMPT_VERSION = "classify_v1"
CLASSIFY_WORKERS = 12
CLASSIFY_TEMPERATURE = 0.0  # deterministic classification

# Deterministic urgency floor: high-stakes phrasing forces urgency >= URGENCY_FLOOR,
# removing model subjectivity on the costliest items. PT-first, kept conservative;
# OR'd with the pre-computed feedback.urgency_floor_signal flag.
URGENCY_FLOOR = 4
URGENCY_FLOOR_PATTERNS = (
    r"\bfraude\b",
    r"cobrad[oa].{0,15}(dobro|duas vezes)",
    r"cobran[çc]a.{0,8}(dobro|indevida)",
    r"nunca cheg\w*",
    r"paguei.{0,25}n[ãa]o.{0,8}receb",
)

# --------------------------------------------------------------------------- #
# Embeddings stage (semantic vectors for themes + RAG retrieval)
# --------------------------------------------------------------------------- #
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536          # native dimensionality of text-embedding-3-small
EMBED_BATCH = 256         # inputs per OpenAI embeddings request
EMBED_HNSW = {"m": 16, "ef_construction": 64}  # pgvector HNSW build params (cosine)

# --------------------------------------------------------------------------- #
# Money-weighting engine (pure SQL/Python — the LLM never emits a figure)
# --------------------------------------------------------------------------- #
# Money is in BRL (the corpus is real Brazilian Olist data). Two clearly-separated
# figures: DIRECT EXPOSURE (deterministic, from real fields) and RETENTION RISK
# (modeled, shown as a low/base/high range and always labeled "modeled estimate").

# Item-impact ranking primitive: impact = severity_weight(urgency) * value * sentiment_mult
# (value = order_value when present, else 1 -> degrades to volume x severity).
SEVERITY_WEIGHT: dict[int, float] = {1: 0.1, 2: 0.3, 3: 0.6, 4: 1.0, 5: 1.5}
SENTIMENT_MULT: dict[str, float] = {"negative": 1.0, "neutral": 0.4, "positive": 0.1}

# Direct-exposure mechanics (deterministic, negatives only).
WISMO_CONTACT_COST = 8.0        # cost of one "where is my order?" support contact
RETURN_COST_FRACTION = 0.15     # reverse-logistics + restocking, as a fraction of order value

# Retention-risk model: retention_risk = customer_value * churn_uplift * category_propensity,
# de-duped to one at-risk customer counted once on their worst (highest-urgency) issue.
CHURN_UPLIFT: dict[str, float] = {"low": 0.05, "base": 0.10, "high": 0.20}
EXPECTED_ANNUAL_ORDERS = 2.5    # CLV proxy: customer_value = order_value * this ...
FLAT_CUSTOMER_VALUE = 150.0     # ... else this flat assumed value (no order money field)
# How strongly a negative in each category predicts churn (0..1). Documented assumption,
# not calibrated from longitudinal data (echo has none) — hence the sensitivity range.
CATEGORY_PROPENSITY: dict[str, float] = {
    "Billing & Payment": 0.90,
    "Shipping & Delivery": 0.70,
    "Returns & Refunds": 0.65,
    "Product Quality": 0.60,
    "Customer Service": 0.65,
    "Pricing & Value": 0.45,
    "Website/App UX": 0.40,
    "Availability & Selection": 0.35,
    "Praise": 0.0,
    "Other/Unclear": 0.0,
}
# Category -> owning team (README taxonomy table). Drives routing in themes/summary/API.
CATEGORY_OWNER: dict[str, str] = {
    "Product Quality": "Merchandising / QA",
    "Shipping & Delivery": "Logistics / Fulfillment",
    "Returns & Refunds": "Reverse-logistics / Finance ops",
    "Billing & Payment": "Payments / Finance / Fraud",
    "Pricing & Value": "Pricing / Merch / Marketing",
    "Website/App UX": "Product / Engineering",
    "Customer Service": "CX / Support ops",
    "Availability & Selection": "Inventory / Buying",
    "Praise": "Marketing / Advocacy",
    "Other/Unclear": "Triage",
}

# Demo week for weekly themes/summary (the busiest week in the Olist span).
DEMO_WEEK = "2018-03-05"

# --------------------------------------------------------------------------- #
# Weekly summary (SQL computes every number; the LLM only narrates)
# --------------------------------------------------------------------------- #
SUMMARY_PROMPT_VERSION = "summary_v2"
SUMMARY_TEMPERATURE = 0.3          # a little warmth for prose; numbers are injected
SUMMARY_TOP_DRIVERS = 5            # top-N drivers (themes fallback: categories by $ at risk)
SUMMARY_URGENT_LIMIT = 10          # urgent items snapshotted into the summary

# --------------------------------------------------------------------------- #
# Themes stage (weekly clustering of embeddings; LLM labels, SQL ranks)
# --------------------------------------------------------------------------- #
THEME_PROMPT_VERSION = "theme_v2"  # v2: forbids stating a specific number pulled from one example only
THEME_TEMPERATURE = 0.2
CLUSTER_DISTANCE_THRESHOLD = 0.40  # agglomerative cosine distance cut (lower = tighter)
MIN_CLUSTER_SIZE = 3               # ignore clusters smaller than this
TOP_THEMES = 10                    # label + keep only the top-N clusters by revenue-at-risk
THEME_SENTIMENTS = ("negative", "neutral")  # cluster actionable items (skip praise/positive)
THEME_LABEL_QUOTES = 6             # representative quotes shown to the labeller per cluster
THEME_LABEL_RETRIES = 2            # generic-label validator retries
# Vague labels the validator rejects (retry with stronger instruction).
THEME_BANNED_LABEL_TERMS = (
    "customer issue", "various", "general", "miscellaneous", "other", "problem with",
    "feedback", "complaint", "issues", "concerns", "multiple", "assorted",
)

# --------------------------------------------------------------------------- #
# RAG stage ("ask echo": embed question -> pgvector top-k -> grounded+cited answer)
# --------------------------------------------------------------------------- #
RAG_PROMPT_VERSION = "rag_v1"
RAG_TEMPERATURE = 0.2   # a little latitude for prose; the model never emits a figure
RAG_TOP_K = 8           # feedback items retrieved per question

# --------------------------------------------------------------------------- #
# Authentication (JWT + role-based access)
# --------------------------------------------------------------------------- #
# Two roles, stored in the `users` table: GEN-POP end users who submit feedback,
# and COMPANY staff who see all feedback + analytics. Tokens are signed HS256.
ROLE_GEN_POP = "gen_pop"
ROLE_COMPANY = "company"
ROLES = (ROLE_GEN_POP, ROLE_COMPANY)
JWT_ALGORITHM = "HS256"
# The insecure default below MUST be overridden in production (set JWT_SECRET in
# .env). config.settings warns at import time when it's still the dev default.
JWT_DEV_SECRET = "dev-insecure-change-me"  # noqa: S105 (documented placeholder, not a real secret)


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
    # JWT auth: signing key + token lifetime. Default secret is insecure on purpose
    # (see JWT_DEV_SECRET) — override with JWT_SECRET in .env for anything real.
    jwt_secret: str = Field(default=JWT_DEV_SECRET, alias="JWT_SECRET")
    jwt_expire_minutes: int = Field(default=720, alias="JWT_EXPIRE_MINUTES")  # 12h
    # Demo credentials seeded by `python -m echo.auth seed` (overridable in .env).
    seed_company_email: str = Field(default="admin@echo.example", alias="SEED_COMPANY_EMAIL")
    seed_company_password: str = Field(default="admin123", alias="SEED_COMPANY_PASSWORD")
    seed_genpop_email: str = Field(default="user@echo.example", alias="SEED_GENPOP_EMAIL")
    seed_genpop_password: str = Field(default="user123", alias="SEED_GENPOP_PASSWORD")

    @property
    def use_offline(self) -> bool:
        """Offline stub generation when explicitly requested or no key present."""
        return self.offline or not self.openai_api_key


settings = Settings()

if settings.jwt_secret == JWT_DEV_SECRET:
    import warnings

    warnings.warn(
        "JWT_SECRET is the insecure dev default — set JWT_SECRET in .env before "
        "deploying (tokens are trivially forgeable otherwise).",
        stacklevel=2,
    )
