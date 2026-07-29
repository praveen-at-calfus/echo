"""The "ask echo" answer contract + prompt: grounded, cited, numberless.

Plain English: the model reads a handful of retrieved customer-feedback
snippets and writes an answer drawn only from them, naming which items it
used. It never states a count, percentage, or dollar figure itself — echo
appends those afterward, computed straight from SQL over the same retrieved
set (the anti-hallucination invariant, same as the weekly summary).

Bump ``RAG_PROMPT_VERSION`` in config when this changes.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field


class RagAnswer(BaseModel):
    """The LLM's grounded answer. Numbers the reader sees come from SQL, not here."""

    answer: str = Field(min_length=10, max_length=1200,
                        description="a grounded answer synthesizing the retrieved snippets; "
                                    "no counts, percentages, or dollar figures")
    cited_item_ids: list[str] = Field(default_factory=list, max_length=10,
                                      description="item_ids (only from the provided snippets) "
                                                  "this answer actually draws on")


SYSTEM = """You are "ask echo" — a Q&A assistant over customer feedback for a Brazilian
e-commerce company. A CX/product leader asks a free-text question; you answer using
ONLY the retrieved feedback snippets provided below.

HARD RULES:
- Ground every claim in the provided snippets. Never use outside knowledge and never
  invent a piece of feedback that isn't there.
- Only cite item_ids that literally appear in the provided snippets.
- NEVER state a count, percentage, or dollar figure yourself — echo computes and appends
  those separately from SQL. If you want to convey scale, use words ("several", "a
  recurring pattern"), not a number.
- If the snippets genuinely don't address the question, say so plainly rather than
  forcing an answer, and leave cited_item_ids empty.
- The feedback is Brazilian Portuguese; you may quote short fragments of it, but write
  your answer in English.
- Be concise and businesslike — a few sentences, not an essay."""


def build_messages(question: str, snippets: list[dict]) -> list[tuple[str, str]]:
    """Build the system + user chat messages sent to the LLM: the ground rules above, plus the retrieved feedback snippets serialized as JSON so the model can read and cite them."""
    # Truncate each snippet's text to keep the prompt short and cheap; the full
    # text isn't needed for the model to understand and cite the item.
    payload = json.dumps(
        [{"item_id": s["item_id"], "source": s["source_type"], "category": s.get("category"),
          "sentiment": s.get("sentiment"), "urgency": s.get("urgency"), "text": s["text"][:500]}
         for s in snippets],
        ensure_ascii=False, indent=2, default=str)
    return [("system", SYSTEM),
            ("user", f'Question: "{question}"\n\nRetrieved feedback snippets (closest meaning first):\n{payload}')]
