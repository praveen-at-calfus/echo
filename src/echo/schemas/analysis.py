"""The classify stage's structured-output contract.

Plain English: this is the exact shape the language model must return for each
feedback item — a routing category, a 3-class sentiment, an urgency 1-5, and a
short reason. Technically: a Pydantic model handed to LangChain's
``with_structured_output`` so the model can't return anything unparseable.

Note the envelope's ``Sentiment`` is only positive/negative (silver labels from
scores); classification needs a **neutral** class too, so it lives here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from echo.schemas.envelope import Category  # the fixed 10 routing categories

AnalysisSentiment = Literal["positive", "neutral", "negative"]
Urgency = Literal[1, 2, 3, 4, 5]


class Classification(BaseModel):
    """One item's analysis, as returned by the LLM (numbers/money added later, in SQL)."""

    category: Category
    sentiment: AnalysisSentiment
    urgency: Urgency
    # A minimum length keeps the model from returning an empty/near-empty reason; a maximum
    # length keeps rationales short and cheap rather than long rambling explanations.
    rationale: str = Field(min_length=3, max_length=400)
