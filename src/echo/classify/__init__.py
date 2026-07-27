"""Classify stage — the brain.

Plain English: for each feedback item, one language-model call decides its
routing category, sentiment, and urgency, and gives a short reason. Results are
written to the versioned ``analysis`` table (and every call is logged to
``llm_calls``). Identical texts reuse a cached result, so it's cheap and
consistent. Run it with ``python -m echo.classify``.
"""
