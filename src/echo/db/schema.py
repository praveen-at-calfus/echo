"""Relational schema for the echo corpus (SQLAlchemy Core).

Three tables:
* ``order_economics`` — one row per order; the money/fulfillment source of truth.
* ``feedback``        — the 15k immutable corpus items (the common envelope,
                        with provenance + messy-flags flattened for SQL filtering
                        AND kept whole as JSONB).
* ``gold_candidates`` — the 40+20 hand-label set (blank label columns).
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

# --------------------------------------------------------------------------- #
# Auth — application users. Two roles: GEN-POP end users who submit feedback,
# and COMPANY staff who read all feedback + analytics. One row per account.
# --------------------------------------------------------------------------- #
users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),          # uuid4, matching the item_id convention
    Column("email", String, nullable=False, unique=True, index=True),  # the login identifier
    Column("password_hash", String, nullable=False),  # bcrypt hash (never the raw password)
    Column("role", String, nullable=False),            # 'gen_pop' | 'company'
    Column("full_name", String),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime, server_default=func.now()),
    CheckConstraint("role IN ('gen_pop', 'company')", name="ck_users_role"),
)

order_economics = Table(
    "order_economics",
    metadata,
    Column("order_id", String, primary_key=True),
    Column("customer_id", String),
    Column("customer_unique_id", String, index=True),
    Column("customer_state", String),
    Column("customer_city", String),
    Column("customer_zip_prefix", String),
    Column("order_status", String, index=True),
    Column("fulfillment_outcome", String, index=True),
    Column("product_value", Numeric),
    Column("freight_value", Numeric),
    Column("order_value", Numeric),
    Column("item_count", Integer),
    Column("product_category_en", String, index=True),
    Column("payment_value_total", Numeric),
    Column("payment_type", String),
    Column("payment_installments", Integer),
    Column("payment_mixed", Boolean),
    Column("refund_amount_proxy", Numeric),
    Column("purchase_ts", DateTime),
    Column("approved_ts", DateTime),
    Column("carrier_ts", DateTime),
    Column("delivered_ts", DateTime),
    Column("estimated_ts", DateTime),
    Column("delivery_days", Float),
    Column("lateness_days", Float),
    Column("delivered_late", Boolean),
    Column("review_score", Integer),
    Column("has_text_review", Boolean),
)

feedback = Table(
    "feedback",
    metadata,
    Column("item_id", String, primary_key=True),
    Column("source_type", String, nullable=False, index=True),
    Column("source_score", Float),
    Column("source_scale", String),
    Column("text", Text, nullable=False),
    Column("created_at", DateTime, index=True),
    Column("language", String),
    Column("order_id", String, ForeignKey("order_economics.order_id"), index=True),
    Column("customer_id", String),
    Column("customer_unique_id", String, index=True),
    Column("order_value", Numeric),
    Column("refund_amount", Numeric),
    Column("freight_value", Numeric),
    Column("payment_type", String),
    Column("product_category_en", String, index=True),
    Column("order_status", String),
    Column("fulfillment_outcome", String),
    Column("lateness_days", Float),
    Column("silver_label", String, index=True),
    Column("silver_label_source", String),
    Column("intended_category", String, index=True),
    Column("synthetic", Boolean, index=True),
    Column("generation_model", String),
    Column("prompt_version", String),
    Column("corpus_build_id", String),
    Column("generation_seed", BigInteger),
    Column("generation_temperature", Float),
    Column("too_long", Boolean),
    Column("spam", Boolean),
    Column("gibberish", Boolean),
    Column("non_target_language", Boolean),
    Column("sarcasm", Boolean),
    Column("off_topic", Boolean),
    Column("multi_topic", Boolean),
    Column("urgency_floor_signal", Boolean),
    Column("condensed", Boolean),
    Column("duplicate_of", String),
    Column("provenance", JSONB),
    Column("messy", JSONB),
    # Who submitted this live (NULL for the 15k batch corpus). Powers GEN-POP
    # users' "view only my own feedback". Added to existing DBs via an idempotent
    # ALTER in the API lifespan hook — create_all won't alter an extant table.
    Column("submitter_id", String, ForeignKey("users.id"), index=True),
)

gold_candidates = Table(
    "gold_candidates",
    metadata,
    Column("item_id", String, ForeignKey("feedback.item_id"), primary_key=True),
    Column("set_name", String, index=True),  # target | reserve
    Column("source_type", String),
    Column("source_score", String),
    Column("source_scale", String),
    Column("intended_category", String),
    Column("product_category_en", String),
    Column("fulfillment_outcome", String),
    Column("messy_flags", String),
    Column("text", Text),
    # --- blank columns for the human labeler ---
    Column("gold_category", String),
    Column("gold_sentiment", String),
    Column("gold_urgency", Integer),
    Column("labeler_notes", Text),
    Column("labeler_id", String),
    Column("labeled_at", DateTime),
)

# --------------------------------------------------------------------------- #
# Pipeline tables — created now, populated by later stages (classify, themes,
# money engine, weekly summary). Empty until those stages ship.
# --------------------------------------------------------------------------- #

# One row per (item x prompt/model version). Versioned: re-running a new prompt
# writes a NEW row, never mutates raw feedback. (README: the analysis store.)
analysis = Table(
    "analysis",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("item_id", String, ForeignKey("feedback.item_id"), nullable=False, index=True),
    Column("category", String, index=True),          # one of the 10 routing categories
    Column("sentiment", String, index=True),          # positive | neutral | negative
    Column("urgency", SmallInteger, index=True),       # 1..5
    Column("rationale", Text),                         # the LLM's explanation
    Column("confidence", Float),                        # optional (bonus)
    Column("source_score_disagreement", Boolean),      # LLM sentiment vs source_score cross-check
    Column("model_name", String, nullable=False),
    Column("prompt_version", String, nullable=False),
    Column("analysis_hash", String, index=True),       # hash(norm_text+prompt_version+model)
    Column("created_at", DateTime, server_default=func.now()),
    UniqueConstraint("item_id", "model_name", "prompt_version", name="uq_analysis_item_version"),
)

# Audit log of every LLM call (classify / condense / theme-label / summary / rag).
llm_calls = Table(
    "llm_calls",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("item_id", String, ForeignKey("feedback.item_id"), index=True),  # nullable
    Column("call_type", String, nullable=False, index=True),
    Column("model_name", String, nullable=False),
    Column("prompt_version", String),
    Column("input", Text),
    Column("output", Text),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("total_tokens", Integer),
    Column("latency_ms", Integer),
    Column("status", String),   # ok | error
    Column("error", Text),
    Column("created_at", DateTime, server_default=func.now()),
)

# Weekly emergent themes (clusters), ranked by revenue-at-risk.
themes = Table(
    "themes",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("week_start", Date, index=True),
    Column("label", String, nullable=False),           # "<component>: <specific issue>"
    Column("category", String, index=True),            # majority routing category
    Column("owner_team", String),
    Column("item_count", Integer),
    Column("direct_exposure", Numeric),                # actual $ (deterministic)
    Column("retention_risk_low", Numeric),             # modeled estimate — sensitivity range
    Column("retention_risk_base", Numeric),
    Column("retention_risk_high", Numeric),
    Column("revenue_at_risk", Numeric, index=True),
    Column("representative_quote", Text),
    Column("representative_item_id", String, ForeignKey("feedback.item_id", ondelete="SET NULL")),
    Column("created_at", DateTime, server_default=func.now()),
)

# Membership: which feedback items belong to which theme cluster.
theme_members = Table(
    "theme_members",
    metadata,
    Column("theme_id", BigInteger, ForeignKey("themes.id", ondelete="CASCADE"), primary_key=True),
    Column("item_id", String, ForeignKey("feedback.item_id", ondelete="CASCADE"), primary_key=True),
)

# The weekly narrative summary — every number injected from SQL; LLM only narrates.
weekly_summary = Table(
    "weekly_summary",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("week_start", Date, unique=True, nullable=False),
    Column("tldr", Text),
    Column("narrative", Text),
    Column("volume_total", Integer),
    Column("volume_prev", Integer),
    Column("sentiment_positive", Integer),
    Column("sentiment_neutral", Integer),
    Column("sentiment_negative", Integer),
    Column("top_themes", JSONB),          # snapshot: top-5 themes + numbers
    Column("urgent_items", JSONB),        # snapshot: urgency >= 4
    Column("recommended_actions", JSONB), # exactly 3, each with $ + owner
    Column("model_name", String),
    Column("prompt_version", String),
    Column("created_at", DateTime, server_default=func.now()),
)
