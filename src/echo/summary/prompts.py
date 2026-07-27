"""The weekly-summary narration contract + prompt.

Plain English: the model is handed a block of already-computed numbers and asked
only to write sentences around them — a TL;DR, a short narrative, and exactly 3
recommended actions. It picks WHICH driver each action targets; echo attaches the
dollar figure and owning team from SQL/config afterward, so the model never emits
a number or invents an owner (the anti-hallucination invariant).

Bump ``SUMMARY_PROMPT_VERSION`` in config when this changes.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field, conlist

from echo.schemas.envelope import Category


class Action(BaseModel):
    """One recommended action. The model chooses the driver + writes the 'what to do';
    echo injects the dollar figure and owner from SQL."""

    category: Category = Field(description="which driver this action targets (from the provided list)")
    recommendation: str = Field(min_length=10, max_length=240,
                                description="the concrete action to take — no dollar figures, no team names")


class WeeklyNarrative(BaseModel):
    """The LLM's narration. Every number the reader sees is injected by echo, not here."""

    tldr: str = Field(min_length=10, max_length=400, description="one or two sentences a leader could read aloud")
    narrative: str = Field(min_length=40, max_length=2200,
                           description="short prose covering volume/sentiment trend, top drivers, and urgent items")
    actions: conlist(Action, min_length=3, max_length=3)  # exactly 3


SYSTEM = """You are echo's weekly Voice-of-Customer analyst for a Brazilian e-commerce company.
You write a crisp weekly briefing for a CX/product leader from PRE-COMPUTED numbers.

HARD RULES:
- Use ONLY the numbers provided in the facts. NEVER invent, compute, or alter a figure.
- Do NOT put specific dollar amounts or team names inside your prose or recommendations —
  echo attaches those from its own data. Refer to drivers by their category name.
- Each of the 3 actions must target one of the provided top-driver categories and say
  concretely what to do (the lever), not restate the number.
- Be specific and businesslike; no fluff. Readable aloud in under 90 seconds.
- The feedback is Brazilian; quotes stay in Portuguese, your narration is in English."""


def build_messages(facts: dict) -> list[tuple[str, str]]:
    payload = json.dumps(facts, ensure_ascii=False, indent=2, default=str)
    return [("system", SYSTEM), ("user", f"Weekly facts (all numbers final):\n{payload}")]
