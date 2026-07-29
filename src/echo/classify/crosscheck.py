"""Sentiment cross-check.

Plain English: reviews and surveys come with a numeric score, so we can sanity-
check the model's sentiment against it — if a 1-star review is called
"positive", that's a disagreement worth flagging (a reliability signal, tracked
in the dashboard). Tickets have no score, so there's nothing to check.
"""

from __future__ import annotations

from echo import config


def _expected(source_score: float | None, source_scale: str | None) -> str | None:
    """Polarity the score implies, or None if the score is neutral / absent."""
    if source_score is None or source_scale is None:
        return None
    if source_scale == "star_1_5":
        # A 1-2 star rating implies the customer is unhappy; 4-5 stars implies they're happy.
        # 3 stars is a genuinely mixed/neutral rating, so it makes no strong prediction.
        if source_score <= 2:
            return "negative"
        if source_score >= 4:
            return "positive"
    elif source_scale == "nps_0_10":
        # Same idea for NPS (0-10 "how likely to recommend us"): low scores are unhappy,
        # high scores are happy, and the middle range is treated as no strong signal.
        if source_score <= config.NPS_NEG_MAX:
            return "negative"
        if source_score >= config.NPS_POS_MIN:
            return "positive"
    return None  # middle band -> no strong expectation


def disagreement(llm_sentiment: str, source_score: float | None, source_scale: str | None) -> bool | None:
    """True/False if the score gives a clear expectation; None otherwise (e.g. tickets)."""
    exp = _expected(source_score, source_scale)
    if exp is None:
        return None
    return llm_sentiment != exp
