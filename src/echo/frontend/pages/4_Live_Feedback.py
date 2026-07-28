"""Live Feedback — submit one new item through the real pipeline (classify + embed + money)."""

from __future__ import annotations

import api_client
import streamlit as st
from common import money_metrics, sidebar_status

st.set_page_config(page_title="echo — Live Feedback", page_icon="✍️", layout="wide")
h = sidebar_status()

st.title("✍️ Live Feedback")
st.caption("Submit a new item and watch it go through the real pipeline — one LLM classification "
          "call, an embedding, and money attached — the same code the batch pipeline uses.")

if not h.get("llm"):
    st.warning("Live submission needs an OpenAI key configured on the backend (OPENAI_API_KEY).")
    st.stop()

with st.form("submit_feedback"):
    text = st.text_area("Feedback text (Portuguese or English)", height=140,
                        placeholder="Ex: O produto chegou com muito atraso e ninguém respondeu meus e-mails.")
    source_type = st.selectbox("Source", ["ticket", "review", "survey"])
    col1, col2 = st.columns(2)
    with col1:
        source_scale = st.selectbox("Score scale (optional)", [None, "star_1_5", "nps_0_10"],
                                    format_func=lambda v: "— none —" if v is None else v)
    with col2:
        source_score = st.number_input("Score (optional)", min_value=0.0, max_value=10.0, value=0.0, step=1.0,
                                       disabled=source_scale is None)

    st.markdown("**Order details** — optional, but the money engine has nothing to compute from without "
               "them (a real support agent would pull these from the linked order; here you type them in).")
    col3, col4, col5 = st.columns(3)
    with col3:
        order_value = st.number_input("Order value (R$)", min_value=0.0, value=0.0, step=10.0)
    with col4:
        refund_amount = st.number_input("Refund / disputed amount (R$)", min_value=0.0, value=0.0, step=10.0)
    with col5:
        fulfillment_outcome = st.selectbox(
            "Fulfillment outcome", [None, "on_time_delivered", "late_delivered", "shipped_not_delivered",
                                    "unavailable", "canceled", "other"],
            format_func=lambda v: "— unknown —" if v is None else v)
    submitted = st.form_submit_button("Submit")

if submitted:
    if not text.strip():
        st.error("Enter some feedback text first.")
        st.stop()
    try:
        result = api_client.submit_feedback(
            text=text, source_type=source_type,
            source_score=(source_score if source_scale else None), source_scale=source_scale,
            order_value=(order_value or None), refund_amount=(refund_amount or None),
            fulfillment_outcome=fulfillment_outcome)
    except Exception as e:  # noqa: BLE001
        st.error(f"Submission failed: {e}")
        st.stop()

    a = result["analysis"]
    st.success(f"Classified: **{a['category']}** · sentiment **{a['sentiment']}** · urgency **{a['urgency']}**"
              + (" ⚑ urgency floor applied" if a.get("floored") else ""))
    st.write(a["rationale"])
    if result.get("source_score_disagreement"):
        st.warning("The model's sentiment disagrees with the given score — flagged for review.")

    st.subheader("Money attached to this item")
    money_metrics(result["money"]["direct_exposure"], result["money"]["retention"],
                 result["money"]["revenue_at_risk"])
    st.caption(f"item_id: {result['item_id']}")
