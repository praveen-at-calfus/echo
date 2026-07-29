"""Weekly Summary - pick a week, generate it live, then it's stored.

Every number is SQL-computed; the LLM only writes the narration and picks which
driver each action targets, then echo attaches the dollar figure and owner.
Generating costs one real LLM call, so it is gated on an API key. Runs via
st.navigation from app.py (router owns page config, auth, sidebar).
"""

from __future__ import annotations

import api_client
import streamlit as st

st.title("Weekly Summary")
st.caption("Every number here is SQL-computed; the LLM only writes the narration and picks which "
           "driver each action targets, then echo attaches the dollar figure and owner.")

try:
    _health = api_client.health()
except Exception:  # noqa: BLE001
    _health = {"llm": False}

if not _health.get("llm"):
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
    st.info("Pick a week and click Generate. Nothing is shown until you do.")
    st.stop()

st.subheader(f"Week of {s['week_start']}")
st.info(s["tldr"])
st.write(s["narrative"])

c1, c2, c3 = st.columns(3)
delta = None
# Only compute a week-over-week percentage change if there was a previous week
# to compare against (otherwise it would divide by zero).
if s["volume_prev"]:
    delta = f"{(s['volume_total'] - s['volume_prev']) / s['volume_prev'] * 100:+.1f}% vs last week"
c1.metric("Volume", f"{s['volume_total']:,}", delta)
c2.metric("Negative", f"{s['sentiment_negative']:,}")
c3.metric("Positive", f"{s['sentiment_positive']:,}")

st.divider()
st.subheader("Top drivers")
for d in s["top_themes"]:
    st.write(f"- **{d['label']}** ({d['category']}, owner {d['owner']}): "
            f"R$ {d['revenue_at_risk']:,.0f} at risk · {d['item_count']} items")

st.divider()
st.subheader("Recommended actions")
for i, a in enumerate(s["recommended_actions"], 1):
    st.write(f"{i}. **[{a['owner']}]** {a['recommendation']} (~R$ {a['revenue_at_risk']:,.0f} at risk)")

with st.expander(f"Urgent items snapshotted this week ({len(s['urgent_items'])})"):
    for it in s["urgent_items"]:
        st.write(f"- R$ {it['exposure']:,.2f} · {it['category']} · urgency {it['urgency']}: {it['snippet']}")
