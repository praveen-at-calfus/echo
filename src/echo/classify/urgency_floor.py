"""Deterministic urgency floor.

Plain English: some phrases mean "this is serious" no matter what the model
thinks — fraud, double-charge, "never arrived". If the text (or the pre-computed
flag) matches, we force urgency to at least 4, so the costliest items can't be
under-ranked by model subjectivity. Technically: regex over normalized text
(config.URGENCY_FLOOR_PATTERNS) OR'd with feedback.urgency_floor_signal.
"""

from __future__ import annotations

import re

from echo import config
from echo.corpus.utils import normalize_text

_PATTERNS = [re.compile(p) for p in config.URGENCY_FLOOR_PATTERNS]


def apply_floor(urgency: int, text: str, floor_signal: bool | None) -> tuple[int, bool]:
    """Return (possibly-raised urgency, whether the floor fired)."""
    hit = bool(floor_signal) or any(p.search(normalize_text(text)) for p in _PATTERNS)
    if hit:
        return max(urgency, config.URGENCY_FLOOR), True
    return urgency, False
