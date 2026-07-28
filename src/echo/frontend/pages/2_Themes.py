"""Themes — recurring issues from weekly clustering, ranked by revenue at risk."""

from __future__ import annotations

import api_client
import streamlit as st
from charts import magnitude_bar
from common import sidebar_status

st.set_page_config(page_title="echo — Themes", page_icon="🧵", layout="wide")
sidebar_status()

st.title("🧵 Themes")
st.caption("Weekly semantic clusters of negative/neutral feedback, labeled by the LLM, ranked by SQL "
          "revenue-at-risk. Retention Risk is a modeled estimate — always a low/base/high range, "
          "never a false-precision single number.")

week = st.text_input("Week (YYYY-MM-DD) — blank for latest available", value="")
limit = st.slider("How many themes", 3, 10, 10,
                  help="The themes stage only keeps the top 10 clusters per week by revenue-at-risk "
                       "(config.TOP_THEMES) — there's nothing beyond 10 to show, even at max.")

try:
    data = api_client.themes(week=week or None, limit=limit)
except Exception as e:  # noqa: BLE001
    st.error(f"Could not reach the echo API: {e}")
    st.stop()

if not data["themes"]:
    st.info("No themes computed yet for this week.")
    st.stop()

st.write(f"Week: **{data['week']}**")
st.plotly_chart(
    magnitude_bar(data["themes"], "label", "revenue_at_risk", "Top themes by revenue at risk",
                 "Revenue at risk (R$)", "Theme", horizontal=True),
    use_container_width=True, theme=None)

for t in data["themes"]:
    with st.expander(f"{t['label']} — R$ {t['revenue_at_risk']:,.0f} at risk ({t['item_count']} items)"):
        st.write(f"**Category:** {t['category']} · **Owner:** {t['owner']}")
        st.write(f"Direct Exposure: R$ {t['direct_exposure']:,.2f}  ·  "
                f"Retention Risk (modeled, low/base/high): R$ {t['retention']['low']:,.0f} — "
                f"R$ {t['retention']['base']:,.0f} — R$ {t['retention']['high']:,.0f}")
        if t["representative_quote"]:
            st.markdown(f"> {t['representative_quote']}")
        st.caption(f"representative item_id: {t['representative_item_id']}")
