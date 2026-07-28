"""Thin HTTP client over the echo API — the frontend's only way to reach data.

Plain English: every number the dashboard shows comes from calling the
backend's JSON endpoints — the frontend never touches the database or an LLM
directly ("thin client" is a hard design rule, not a suggestion). Reads are
cached for a minute so clicking between pages doesn't refetch data that can't
have changed that fast; writes (submitting feedback, asking a question) are
never cached.
"""

from __future__ import annotations

import os

import httpx
import streamlit as st

BASE_URL = os.environ.get("ECHO_API_URL", "http://localhost:8000")
_TIMEOUT = 30.0


def _get(path: str, **params) -> dict:
    params = {k: v for k, v in params.items() if v is not None}
    r = httpx.get(f"{BASE_URL}{path}", params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _post(path: str, payload: dict) -> dict:
    r = httpx.post(f"{BASE_URL}{path}", json=payload, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=60)
def health() -> dict:
    return _get("/health")


@st.cache_data(ttl=60)
def overview() -> dict:
    return _get("/stats/overview")


@st.cache_data(ttl=60)
def volume(by: str = "category") -> dict:
    return _get("/stats/volume", by=by)


@st.cache_data(ttl=60)
def sentiment(by: str = "split") -> dict:
    return _get("/stats/sentiment", by=by)


@st.cache_data(ttl=60)
def crosstab() -> dict:
    return _get("/stats/crosstab")


@st.cache_data(ttl=60)
def themes(week: str | None = None, limit: int = 10) -> dict:
    return _get("/themes", week=week, limit=limit)


@st.cache_data(ttl=60)
def urgent(week: str | None = None, limit: int = 20) -> dict:
    return _get("/urgent", week=week, limit=limit)


@st.cache_data(ttl=60)
def weekly_summary(week: str | None = None) -> dict:
    return _get("/summary/weekly", week=week)


def submit_feedback(text: str, source_type: str = "ticket",
                    source_score: float | None = None, source_scale: str | None = None) -> dict:
    """Live POST — never cached (writes data + costs a real LLM call)."""
    return _post("/feedback", {"text": text, "source_type": source_type,
                               "source_score": source_score, "source_scale": source_scale})


def ask(question: str, k: int | None = None) -> dict:
    """Live POST — never cached (each question deserves a fresh retrieval)."""
    payload: dict = {"question": question}
    if k:
        payload["k"] = k
    return _post("/ask", payload)
