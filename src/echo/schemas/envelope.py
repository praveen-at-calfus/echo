"""The common feedback envelope — echo's canonical corpus item.

``CorpusItem`` is the single normalized shape every source (review / ticket /
survey) maps into, and is reused downstream by the ingest loader and the
money engine. Its validators enforce the README invariants at the type level:

* a ticket has **no** score; reviews and surveys always carry one;
* scores stay in range for their scale;
* ``created_at`` sits inside the real Olist activity window;
* ``intended_category`` is drawn from the fixed 10-category taxonomy;
* every money/date field is carried, never computed here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, get_args

from pydantic import BaseModel, Field, model_validator

from echo import config

SourceType = Literal["review", "ticket", "survey"]
SourceScale = Literal["star_1_5", "nps_0_10"]
Sentiment = Literal["negative", "positive"]
FulfillmentOutcome = Literal[
    "on_time_delivered",
    "late_delivered",
    "shipped_not_delivered",
    "canceled",
    "unavailable",
    "other",
]
Category = Literal[
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
]

# Guard against the taxonomy drifting apart from config.CATEGORIES.
assert set(get_args(Category)) == set(config.CATEGORIES), "Category literal out of sync with config"

_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://echo.local/corpus")


def make_item_id(source_type: str, source_ref: str) -> str:
    """Stable, idempotent id so re-runs never grow duplicates."""
    return str(uuid.uuid5(_ID_NAMESPACE, f"{source_type}:{source_ref}"))


class Provenance(BaseModel):
    """Where an item came from — real vs synthesized, and how it was made."""

    synthetic: bool
    grounded_on: str | None = None  # order_id (or review_id for reviews)
    generation_model: str | None = None
    prompt_version: str | None = None
    generation_seed: int | None = None
    generation_temperature: float | None = None
    generated_at: datetime | None = None
    builder_version: str = config.BUILDER_VERSION
    corpus_build_id: str | None = None


class MessyFlags(BaseModel):
    """First-class messy-input signals detected or injected at build time."""

    too_short: bool = False
    too_long: bool = False
    spam: bool = False
    gibberish: bool = False
    non_target_language: bool = False
    sarcasm: bool = False
    off_topic: bool = False
    multi_topic: bool = False
    duplicate_of: str | None = None
    condensed: bool = False  # always False at build; downstream condenses too_long
    urgency_floor_signal: bool = False


class CorpusItem(BaseModel):
    """One normalized feedback item in the common envelope."""

    item_id: str
    source_type: SourceType
    source_score: float | None = None
    source_scale: SourceScale | None = None
    text: str
    created_at: datetime
    language: str = "pt"

    # Money / order context (carried from order_economics, never computed here).
    order_id: str | None = None
    customer_id: str | None = None
    customer_unique_id: str | None = None
    order_value: float | None = None
    refund_amount: float | None = None
    freight_value: float | None = None
    payment_type: str | None = None
    product_category_en: str | None = None
    order_status: str | None = None
    fulfillment_outcome: FulfillmentOutcome | None = None
    lateness_days: float | None = None

    # Weak / silver labels (never gold ground truth).
    silver_label: Sentiment | None = None
    silver_label_source: str | None = None
    intended_category: Category | None = None  # synthesis hint, NOT gold truth

    provenance: Provenance
    messy: MessyFlags = Field(default_factory=MessyFlags)

    @model_validator(mode="after")
    def _check_invariants(self) -> CorpusItem:
        # Source distinction: tickets have no score; review/survey always do.
        if self.source_type == "ticket":
            if self.source_score is not None or self.source_scale is not None:
                raise ValueError("tickets must have source_score=None (NL-only)")
        else:
            if self.source_score is None or self.source_scale is None:
                raise ValueError(f"{self.source_type} must carry a source_score + scale")

        # Score range per scale.
        if self.source_scale == "star_1_5" and not (1 <= self.source_score <= 5):
            raise ValueError("star score out of range 1..5")
        if self.source_scale == "nps_0_10" and not (0 <= self.source_score <= 10):
            raise ValueError("NPS score out of range 0..10")

        # Text presence: only score-only surveys may be empty.
        if not self.text.strip():
            if not (self.source_type == "survey" and self.messy.too_short):
                raise ValueError("empty text only allowed for score-only surveys")

        # Date window.
        if not (config.DATE_MIN <= self.created_at.replace(tzinfo=None) <= config.DATE_MAX):
            raise ValueError(f"created_at {self.created_at} outside Olist window")

        return self


def export_json_schema(path) -> None:
    """Write the Pydantic-derived JSON Schema for the downstream loader."""
    import json

    path.write_text(json.dumps(CorpusItem.model_json_schema(), indent=2, ensure_ascii=False))
