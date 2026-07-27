"""The theme-labelling prompt + a generic-label validator.

Plain English: given a handful of real quotes from one cluster (all roughly the
same complaint) and the cluster's routing category, the model writes a short,
specific label in the form ``<component>: <specific issue>`` — e.g.
"Checkout: payment fails on final step". A validator rejects vague labels like
"customer issues" and we retry with a firmer instruction.

Bump ``THEME_PROMPT_VERSION`` in config when this changes.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from echo import config


class ThemeLabel(BaseModel):
    """One cluster's label. Format enforced downstream by the validator."""

    label: str = Field(min_length=5, max_length=70,
                       description="'<component>: <specific issue>', specific and concrete")


SYSTEM = """You name recurring themes in Brazilian e-commerce customer feedback.
You are shown several real quotes (Portuguese) that all belong to ONE cluster, plus the
cluster's routing category. Write ONE short, SPECIFIC label in English, in the exact form:

  <component>: <specific issue>

Rules:
- Name the concrete thing and what's wrong: "Checkout: card declined on final step",
  "Delivery: package arrives damaged", "Refund: not issued after cancellation".
- Be specific to what the quotes actually say. NEVER vague ("customer issues", "various
  problems", "general complaints", "product feedback").
- 3-9 words total. No dollar amounts, no counts, no team names.
- English label even though the quotes are Portuguese."""


def build_messages(category: str, quotes: list[str], stricter: bool = False) -> list[tuple[str, str]]:
    body = "\n".join(f"- {q}" for q in quotes)
    extra = ("\n\nYour previous label was too generic. Be concrete and specific to these quotes: "
             "name the exact component and the exact failure." if stricter else "")
    return [("system", SYSTEM),
            ("user", f"Routing category: {category}\nQuotes from this cluster:\n{body}{extra}")]


def is_generic(label: str, category: str) -> bool:
    """True if the label is too vague/generic to keep (triggers a retry)."""
    low = label.lower().strip()
    if ":" not in label:
        return True  # must be "<component>: <issue>"
    component, _, issue = label.partition(":")
    if len(issue.strip()) < 3 or len(component.strip()) < 2:
        return True
    if low == category.lower() or low.rstrip("s") == category.lower().rstrip("s"):
        return True  # just restating the category
    return any(term in low for term in config.THEME_BANNED_LABEL_TERMS)
