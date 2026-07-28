"""Weekly Summary — the SQL-computed briefing, narrated by the LLM."""

from __future__ import annotations

import api_client
import streamlit as st
from common import sidebar_status

st.set_page_config(page_title="echo — Weekly Summary", page_icon="🗞️", layout="wide")
sidebar_status()

st.title("🗞️ Weekly Summary")
st.caption("Every number here is SQL-computed; the LLM only writes the narration and picks which "
          "driver each action targets — echo attaches the dollar figure and owner afterward.")

week = st.text_input("Week (YYYY-MM-DD) — blank for latest available", value="")

try:
    s = api_client.weekly_summary(week=week or None)
except Exception as e:  # noqa: BLE001
    st.error(f"No weekly summary available: {e}")
    st.stop()

st.subheader(f"Week of {s['week_start']}")
st.info(s["tldr"])
st.write(s["narrative"])

c1, c2, c3 = st.columns(3)
delta = None
if s["volume_prev"]:
    delta = f"{(s['volume_total'] - s['volume_prev']) / s['volume_prev'] * 100:+.1f}% vs last week"
c1.metric("Volume", f"{s['volume_total']:,}", delta)
c2.metric("Negative", f"{s['sentiment_negative']:,}")
c3.metric("Positive", f"{s['sentiment_positive']:,}")

st.divider()
st.subheader("Top drivers")
for d in s["top_themes"]:
    st.write(f"- **{d['label']}** ({d['category']}, owner {d['owner']}) — "
            f"R$ {d['revenue_at_risk']:,.0f} at risk · {d['item_count']} items")

st.divider()
st.subheader("Recommended actions")
for i, a in enumerate(s["recommended_actions"], 1):
    st.write(f"{i}. **[{a['owner']}]** {a['recommendation']} (~R$ {a['revenue_at_risk']:,.0f} at risk)")

with st.expander(f"Urgent items snapshotted this week ({len(s['urgent_items'])})"):
    for it in s["urgent_items"]:
        st.write(f"- R$ {it['exposure']:,.2f} · {it['category']} · urgency {it['urgency']} — {it['snippet']}")
