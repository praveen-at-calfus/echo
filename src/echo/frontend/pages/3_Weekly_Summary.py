"""Weekly Summary — pick a week, generate it live, then it's stored.

Every number is SQL-computed; the LLM only writes the narration and picks which
driver each action targets — echo attaches the dollar figure and owner
afterward. Generating costs one real LLM call, so it's gated on an API key,
same as Live Feedback and Ask echo.
"""

from __future__ import annotations

import api_client
import streamlit as st
from common import sidebar_status

st.set_page_config(page_title="echo — Weekly Summary", page_icon="🗞️", layout="wide")
h = sidebar_status()

st.title("🗞️ Weekly Summary")
st.caption("Every number here is SQL-computed; the LLM only writes the narration and picks which "
          "driver each action targets — echo attaches the dollar figure and owner afterward.")

if not h.get("llm"):
    st.warning("Generating a weekly summary needs an OpenAI key configured on the backend (OPENAI_API_KEY).")
    st.stop()

week = st.text_input("Week (YYYY-MM-DD)", value="", placeholder="e.g. 2018-03-05")
generate = st.button("Generate weekly summary", type="primary")

if "weekly_summary_result" not in st.session_state:
    st.session_state.weekly_summary_result = None

if generate:
    if not week.strip():
        st.error("Enter a week first (YYYY-MM-DD).")
        st.stop()
    with st.spinner(f"Computing every number for {week} and asking the model to narrate..."):
        try:
            st.session_state.weekly_summary_result = api_client.generate_weekly_summary(week)
        except Exception as e:  # noqa: BLE001
            st.error(f"Couldn't generate a summary for {week}: {e}")
            st.stop()

s = st.session_state.weekly_summary_result
if s is None:
    st.info("Pick a week and click Generate — nothing is shown until you do.")
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
