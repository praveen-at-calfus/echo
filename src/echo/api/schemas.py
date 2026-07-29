"""Request/response shapes for the API (light — most reads return plain dicts)."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

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


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
# A pragmatic email check (no email-validator dependency): one @, a dot in the
# domain. Good enough to reject obvious junk without pulling in a new library.
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

# Password policy for public self-registration (see RegisterIn._check_password).
# Kept off the admin CLI / seed path on purpose — staff provisioning shouldn't be
# blocked by a policy meant for public sign-up.
_SPECIAL_CHARS = r"!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?~`"
PASSWORD_MIN_LENGTH = 8


class RegisterIn(BaseModel):
    """Public self-registration (POST /auth/register). Always creates a GEN-POP."""

    email: str = Field(pattern=_EMAIL_PATTERN, max_length=254)
    password: str = Field(max_length=200)
    full_name: str | None = Field(default=None, max_length=120)

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        """Pydantic validator: rejects passwords that don't meet the length/case/digit/special-character policy, listing everything missing in one error."""
        # Check every rule instead of stopping at the first failure, so the
        # error message tells the user everything they need to fix at once.
        missing = []
        if len(v) < PASSWORD_MIN_LENGTH:
            missing.append(f"at least {PASSWORD_MIN_LENGTH} characters")
        if not re.search(r"[A-Z]", v):
            missing.append("an uppercase letter")
        if not re.search(r"[a-z]", v):
            missing.append("a lowercase letter")
        if not re.search(r"\d", v):
            missing.append("a number")
        if not re.search(f"[{_SPECIAL_CHARS}]", v):
            missing.append("a special character")
        if missing:
            raise ValueError("Password must contain " + ", ".join(missing) + ".")
        return v


class TokenOut(BaseModel):
    """What /auth/login and /auth/register return: a bearer token + the role."""

    access_token: str
    token_type: str = "bearer"
    role: str


class UserOut(BaseModel):
    """The current user (GET /auth/me) — never includes the password hash."""

    id: str
    email: str
    role: str
    full_name: str | None = None
