"""Ask echo — grounded, cited Q&A over the feedback corpus (RAG, bonus)."""

from __future__ import annotations

import api_client
import streamlit as st
from common import sidebar_status

st.set_page_config(page_title="echo — Ask echo", page_icon="💬", layout="wide")
h = sidebar_status()

st.title("💬 Ask echo")
st.caption("Ask a free-text question. echo retrieves the closest-meaning feedback and writes a "
          "grounded, cited answer — every number in the reply is computed in SQL over the retrieved "
          "items, never invented by the model.")

if not h.get("llm"):
    st.warning("Ask echo needs an OpenAI key configured on the backend (OPENAI_API_KEY).")
    st.stop()

question = st.text_input("Your question", placeholder="e.g. what are customers saying about late deliveries?")
k = st.slider("How many feedback items to retrieve", 3, 20, 8)

if st.button("Ask", type="primary") and question.strip():
    with st.spinner("Retrieving feedback and drafting an answer..."):
        try:
            result = api_client.ask(question, k=k)
        except Exception as e:  # noqa: BLE001
            st.error(f"Ask failed: {e}")
            st.stop()

    st.markdown(f"### {result['answer']}")

    if result.get("stats"):
        s = result["stats"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Items retrieved", s["n_retrieved"])
        c2.metric("Top category", s["top_category"] or "—", help=s["top_category"] or "no category")
        c3.metric("Direct Exposure", f"R$ {s['direct_exposure']:,.0f}")
        c4.metric("Revenue at risk", f"R$ {s['revenue_at_risk']:,.0f}")
        st.caption(f"Sentiment among retrieved items: {s['sentiment']}")

    if result["citations"]:
        st.subheader("Sources")
        for c in result["citations"]:
            with st.expander(f"[{c['source_type']}] {c['item_id']}"):
                st.write(c["snippet"])
    else:
        st.caption("No specific items were cited.")
