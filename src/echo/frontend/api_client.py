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


class ApiError(Exception):
    """A friendly, already-explained API failure to show the user directly."""


def _headers() -> dict:
    """Attach the logged-in user's bearer token (if any) to every request."""
    user = st.session_state.get("auth_user")
    if user and user.get("token"):
        return {"Authorization": f"Bearer {user['token']}"}
    return {}


def _raise_for_auth(r: httpx.Response) -> None:
    """Map auth failures to a clear message; expire the session on a 401."""
    if r.status_code == 401:
        st.session_state.pop("auth_user", None)  # force a fresh login
        raise ApiError("Your session has expired. Please log in again.")
    if r.status_code == 403:
        raise ApiError("Your account isn't allowed to view this resource.")
    r.raise_for_status()


def _get(path: str, **params) -> dict:
    params = {k: v for k, v in params.items() if v is not None}
    r = httpx.get(f"{BASE_URL}{path}", params=params, headers=_headers(), timeout=_TIMEOUT)
    _raise_for_auth(r)
    return r.json()


def _post(path: str, payload: dict) -> dict:
    r = httpx.post(f"{BASE_URL}{path}", json=payload, headers=_headers(), timeout=_TIMEOUT)
    _raise_for_auth(r)
    return r.json()


# --------------------------------------------------------------------------- #
# Auth — login/register/whoami. Not cached (each returns a per-user token).
# --------------------------------------------------------------------------- #
def login(email: str, password: str) -> dict:
    """OAuth2 password form (username = email). Returns {access_token, role}."""
    r = httpx.post(f"{BASE_URL}/auth/login",
                   data={"username": email, "password": password}, timeout=_TIMEOUT)
    if r.status_code == 401:
        raise ApiError("Incorrect email or password.")
    r.raise_for_status()
    return r.json()


def _validation_message(r: httpx.Response) -> str:
    """Turn a FastAPI/Pydantic 422 error body into one readable sentence."""
    try:
        detail = r.json().get("detail", [])
        msgs = [(d.get("msg", "") if isinstance(d, dict) else str(d)).removeprefix("Value error, ")
                for d in detail]
        if msgs:
            return " ".join(msgs)
    except Exception:  # noqa: BLE001
        pass
    return "Enter a valid email and a stronger password."


def register(email: str, password: str, full_name: str | None = None) -> dict:
    """Self-register a feedback-giver (GEN-POP) account. Returns the created user
    (id, email, role, full_name) - no token. Sign-up no longer logs the user in
    automatically; they are sent back to the login form instead."""
    r = httpx.post(f"{BASE_URL}/auth/register",
                   json={"email": email, "password": password, "full_name": full_name},
                   timeout=_TIMEOUT)
    if r.status_code == 409:
        raise ApiError("That email is already registered. Try logging in instead.")
    if r.status_code == 422:
        raise ApiError(_validation_message(r))
    r.raise_for_status()
    return r.json()


def me() -> dict:
    return _get("/auth/me")


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


def generate_weekly_summary(week: str) -> dict:
    """Live POST — never cached (costs a real LLM call; generate-then-store, not read-cached)."""
    return _post("/summary/weekly", {"week": week})


@st.cache_data(ttl=60)
def eval_gold() -> dict:
    return _get("/eval/gold")


@st.cache_data(ttl=60)
def users_analytics() -> dict:
    """Company-only: per-user submission behavior (User Analytics page)."""
    return _get("/users/analytics")


def submit_feedback(text: str, source_type: str = "ticket",
                    source_score: float | None = None, source_scale: str | None = None,
                    order_value: float | None = None, refund_amount: float | None = None,
                    fulfillment_outcome: str | None = None) -> dict:
    """Live POST — never cached (writes data + costs a real LLM call)."""
    return _post("/feedback", {
        "text": text, "source_type": source_type, "source_score": source_score, "source_scale": source_scale,
        "order_value": order_value, "refund_amount": refund_amount, "fulfillment_outcome": fulfillment_outcome})


def list_feedback(limit: int = 50, offset: int = 0, **filters) -> dict:
    """GET /feedback — deliberately UNCACHED: gen_pop users see only their own
    rows, and st.cache_data is process-global across browser sessions, so caching
    a per-user list would leak one user's submissions to another."""
    return _get("/feedback", limit=limit, offset=offset, **filters)


def ask(question: str, k: int | None = None) -> dict:
    """Live POST — never cached (each question deserves a fresh retrieval)."""
    payload: dict = {"question": question}
    if k:
        payload["k"] = k
    return _post("/ask", payload)
