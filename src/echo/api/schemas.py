"""Request/response shapes for the API (light — most reads return plain dicts)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FeedbackIn(BaseModel):
    """A single live feedback submission (POST /feedback)."""

    text: str = Field(min_length=1, max_length=4000)
    source_type: Literal["review", "ticket", "survey"] = "ticket"
    source_score: float | None = Field(default=None, description="rating/NPS if the source carries one")
    source_scale: Literal["star_1_5", "nps_0_10"] | None = None
