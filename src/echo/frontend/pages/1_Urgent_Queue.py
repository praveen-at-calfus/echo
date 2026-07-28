"""Urgent Queue — items at/above the urgency floor, ranked by $ exposure."""

from __future__ import annotations

import api_client
import streamlit as st
from common import sidebar_status

st.set_page_config(page_title="echo — Urgent Queue", page_icon="🚨", layout="wide")
sidebar_status()

st.title("🚨 Urgent Queue")
st.caption("Urgency ≥ floor, ranked by per-item Direct Exposure — the SQL-computed dollar cost of that item.")

week = st.text_input("Week (YYYY-MM-DD) — blank for all-time", value="")
limit = st.slider("How many items", 5, 100, 20)

try:
    data = api_client.urgent(week=week or None, limit=limit)
except Exception as e:  # noqa: BLE001
    st.error(f"Could not reach the echo API: {e}")
    st.stop()

st.write(f"**{data['count']}** urgent items · week: {data['week']}")

if not data["items"]:
    st.info("No urgent items for this week.")
    st.stop()

for it in data["items"]:
    header = (f"R$ {it['exposure']:,.2f} · {it['category']} · urgency {it['urgency']} · {it['source_type']}")
    with st.expander(header):
        st.write(it["snippet"])
        st.caption(f"item_id: {it['item_id']} · owner: {it['owner']} · order value: R$ {it['order_value']:,.2f}")
