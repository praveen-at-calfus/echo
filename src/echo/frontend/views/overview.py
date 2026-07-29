"""Overview - the company home screen: headline numbers + where volume comes from.

Plain English: the first thing a CX/product leader sees: how much feedback, how
negative, how much money is at stake, and where the volume comes from. Every
figure is a straight read of the backend's /stats/* endpoints; nothing is
computed in the frontend. Runs via st.navigation from app.py (router owns page
config, auth, sidebar).
"""

from __future__ import annotations

import api_client
import streamlit as st
from charts import crosstab_heatmap, magnitude_bar, sentiment_split_bar, sentiment_trend
from common import money_metrics

st.title("echo - customer-feedback intelligence")
st.caption("AI-classified reviews, tickets and surveys, dollar-weighted and ranked by revenue at risk.")

try:
    ov = api_client.overview()
except Exception as e:  # noqa: BLE001
    st.error(f"Could not reach the echo API at {api_client.BASE_URL}: {e}")
    st.stop()

TIER_LABELS = {
    "T0": "text-only", "T1": "+ scores", "T2": "+ order value",
    "T3": "full money (order value + refund + customer id)",
}

c1, c2, c3, c4 = st.columns(4)
c1.metric("Feedback items", f"{ov['items']:,}")
c2.metric("Negative share", f"{ov['negative_share_pct']:.1f}%")
c3.metric("Urgent items", f"{ov['urgent']:,}")
c4.metric("Money data tier", ov["tier"], help=TIER_LABELS.get(ov["tier"], ""))

st.divider()
st.subheader("Money at stake (all-time)")
money_metrics(ov["direct_exposure"], ov["retention"])

st.divider()
left, right = st.columns(2)
with left:
    st.subheader("Volume by category")
    vol_cat = api_client.volume(by="category")
    st.plotly_chart(
        magnitude_bar(vol_cat["data"], "key", "count", "Feedback volume by category", "Items", "Category",
                     horizontal=True),
        use_container_width=True, theme=None)
with right:
    st.subheader("Volume by source")
    vol_src = api_client.volume(by="source")
    st.plotly_chart(
        magnitude_bar(vol_src["data"], "key", "count", "Feedback volume by source", "Source", "Items"),
        use_container_width=True, theme=None)

st.divider()
left2, right2 = st.columns(2)
with left2:
    st.subheader("Sentiment split")
    sent = api_client.sentiment(by="split")
    st.plotly_chart(sentiment_split_bar(sent["data"]), use_container_width=True, theme=None)
with right2:
    st.subheader("Sentiment trend (by week)")
    trend = api_client.sentiment(by="week")
    if trend["data"]:
        st.plotly_chart(sentiment_trend(trend["data"]), use_container_width=True, theme=None)
    else:
        st.info("Not enough weekly data yet.")

st.divider()
st.subheader("Category by source")
st.caption('Which categories are ticket-heavy vs. survey-heavy, e.g. "Shipping & Delivery" issues '
          "surface mostly as support tickets.")
ct = api_client.crosstab()
if ct["data"]:
    st.plotly_chart(crosstab_heatmap(ct["data"]), use_container_width=True, theme=None)
