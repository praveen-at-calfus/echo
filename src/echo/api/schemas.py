"""Request/response shapes for the API (light — most reads return plain dicts)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FulfillmentOutcome = Literal[
    "on_time_delivered", "late_delivered", "shipped_not_delivered", "unavailable", "canceled", "other",
]


class FeedbackIn(BaseModel):
    """A single live feedback submission (POST /feedback).

    order_value/refund_amount/fulfillment_outcome are optional — a real support
    intake would look these up from the linked order; here they're typed in
    directly. Without them, the money engine has no dollar figure to attach (an
    unlinked live item degrades to $0 exposure, never a fabricated one).
    """

    text: str = Field(min_length=1, max_length=4000)
    source_type: Literal["review", "ticket", "survey"] = "ticket"
    source_score: float | None = Field(default=None, description="rating/NPS if the source carries one")
    source_scale: Literal["star_1_5", "nps_0_10"] | None = None
    order_value: float | None = Field(default=None, ge=0, description="the order's R$ value, if known")
    refund_amount: float | None = Field(default=None, ge=0, description="R$ refund/disputed amount, if known")
    fulfillment_outcome: FulfillmentOutcome | None = Field(
        default=None, description="what happened to the order, if this is a shipping complaint")


class AskIn(BaseModel):
    """A free-text question for "ask echo" (POST /ask)."""

    question: str = Field(min_length=3, max_length=500)
    k: int | None = Field(default=None, ge=1, le=20, description="override how many items to retrieve")


class WeeklySummaryIn(BaseModel):
    """Which week to generate a weekly summary for (POST /summary/weekly)."""

    week: str = Field(description="ISO week start, e.g. 2018-03-05", pattern=r"^\d{4}-\d{2}-\d{2}$")
