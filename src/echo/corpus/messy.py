"""Deterministic messy-input planning.

Real Olist reviews are uniformly short and clean (<=208 chars), so the messy
variety echo's Stage-0 must handle — long rants, spam, gibberish, code-switch,
sarcasm, multi-topic, near-dupes — is injected here, into the *synthetic*
tickets/surveys. Each plan is drawn from a per-item seeded RNG so the total
messy budget is fixed and reproducible; realized counts are reported in the
manifest.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from echo import config

_NON_PT = ("en", "es", "pt-en-mix")


@dataclass
class MessyPlan:
    too_long: bool = False
    spam: bool = False
    gibberish: bool = False
    non_target_language: bool = False
    language: str = "pt"
    sarcasm: bool = False
    off_topic: bool = False
    multi_topic: bool = False
    near_duplicate: bool = False
    score_only: bool = False
    target_length: int = 300  # chars; a soft target handed to the generator
    directives: list[str] = field(default_factory=list)


def plan_ticket(rng: np.random.Generator) -> MessyPlan:
    f = config.MESSY_FRACTIONS_TICKETS
    p = MessyPlan()

    r = rng.random()
    if r < f["very_long"]:
        p.too_long, p.target_length = True, int(rng.integers(2000, 3500))
        p.directives.append("Write a very long, rambling message (multiple paragraphs).")
    elif r < f["very_long"] + f["long"]:
        p.too_long, p.target_length = True, int(rng.integers(1000, 2000))
        p.directives.append("Write a long, detailed message with background and repetition.")
    else:
        p.target_length = int(rng.integers(120, 520))

    if rng.random() < f["spam"]:
        p.spam = True
        p.directives.append("Make this look like promotional spam unrelated to support.")
    if rng.random() < f["gibberish"]:
        p.gibberish = True
        p.directives.append("Produce near-gibberish: broken, keyboard-mashed, barely coherent.")
    if rng.random() < f["non_target_language"]:
        p.non_target_language = True
        p.language = _NON_PT[int(rng.integers(0, len(_NON_PT)))]
        p.directives.append(f"Write in language style: {p.language} (do not translate to Portuguese).")
    if rng.random() < f["sarcasm"]:
        p.sarcasm = True
        p.directives.append("Use heavy sarcasm/irony rather than stating the issue plainly.")
    if rng.random() < f["off_topic"]:
        p.off_topic = True
        p.directives.append("Drift largely off-topic; the actual issue is unclear.")
    if rng.random() < f["multi_topic"]:
        p.multi_topic = True
        p.directives.append("Complain about two unrelated problems in the same message.")
    if rng.random() < f["near_duplicate"]:
        p.near_duplicate = True
    return p


def plan_survey(rng: np.random.Generator) -> MessyPlan:
    f = config.MESSY_FRACTIONS_SURVEYS
    p = MessyPlan(target_length=int(rng.integers(20, 200)))
    if rng.random() < f["score_only"]:
        p.score_only = True
    if rng.random() < f["non_target_language"]:
        p.non_target_language = True
        p.language = _NON_PT[int(rng.integers(0, len(_NON_PT)))]
        p.directives.append(f"Write in language style: {p.language}.")
    if rng.random() < f["near_duplicate"]:
        p.near_duplicate = True
    return p
