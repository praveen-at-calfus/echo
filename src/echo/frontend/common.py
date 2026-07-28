"""Small UI bits shared by every page: the sidebar health badge and the
Direct-Exposure/Retention-Risk metric row (repeated on Overview, Themes,
Urgent, Live Feedback, and Ask echo)."""

from __future__ import annotations

import api_client
import streamlit as st


def sidebar_status() -> dict:
    """Show DB/LLM health in the sidebar; return the health dict for page-level gating."""
    try:
        h = api_client.health()
    except Exception as e:  # noqa: BLE001
        st.sidebar.error(f"API unreachable at {api_client.BASE_URL}: {e}")
        return {"status": "unreachable", "db": False, "llm": False, "build_id": None}
    st.sidebar.markdown("### System status")
    st.sidebar.write(("🟢" if h["db"] else "🔴") + " Database")
    st.sidebar.write(("🟢" if h["llm"] else "⚪") + " Live LLM features" + ("" if h["llm"] else " (no API key)"))
    st.sidebar.caption(f"build: {h.get('build_id') or '—'}")
    return h


def _compact(n: float) -> str:
    """R$ figure, abbreviated to fit a narrow metric tile (exact value goes in `help`)."""
    n = float(n)
    if abs(n) >= 1_000_000:
        return f"R$ {n / 1_000_000:,.1f}M"
    if abs(n) >= 1_000:
        return f"R$ {n / 1_000:,.0f}k"
    return f"R$ {n:,.0f}"


def money_metrics(direct_exposure: float, retention: dict, revenue_at_risk: float | None = None) -> None:
    """Direct Exposure (deterministic) + Retention Risk (modeled low/base/high) as metric tiles."""
    cols = st.columns(4 if revenue_at_risk is not None else 3)
    cols[0].metric("Direct Exposure", _compact(direct_exposure),
                   help=f"Deterministic — actual $ from real fields (refunds, disputes, lost orders). "
                        f"Exact: R$ {direct_exposure:,.2f}")
    cols[1].metric("Retention Risk (base)", _compact(retention["base"]),
                   help=f"Modeled estimate, not measured — see the range for sensitivity. "
                        f"Exact: R$ {retention['base']:,.2f}")
    low, high = _compact(retention["low"]), _compact(retention["high"])
    cols[2].metric("Retention range (low-high)", f"{low} – {high.replace('R$ ', '')}")
    if revenue_at_risk is not None:
        cols[3].metric("Revenue at risk", _compact(revenue_at_risk),
                       help=f"Direct Exposure + Retention Risk (base). Exact: R$ {revenue_at_risk:,.2f}")
