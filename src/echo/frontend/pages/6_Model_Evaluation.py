"""Model Evaluation — gold-set confusion matrix + at-scale silver-sentiment accuracy.

Two report cards, not one, because they check different things: the 40-item
gold set is the only real ground truth for CATEGORY (human-verified), while the
silver-sentiment check reuses star/NPS scores across thousands of items for a
much larger (if weaker per-item) sentiment-accuracy sample.
"""

from __future__ import annotations

import api_client
import pandas as pd
import streamlit as st
from common import sidebar_status

st.set_page_config(page_title="echo — Model Evaluation", page_icon="🎯", layout="wide")
sidebar_status()

st.title("🎯 Model Evaluation")
st.caption("How well the classifier's actual output matches ground truth, at two different scales. "
          "Every number here is a plain SQL/Python aggregate over already-classified data — no LLM "
          "call, nothing invented.")

try:
    data = api_client.eval_gold()
except Exception as e:  # noqa: BLE001
    st.error(f"Could not reach the echo API: {e}")
    st.stop()

gold = data["gold"]
silver = data["silver_sentiment"]

if not gold.get("labeled"):
    st.info("No labeled gold-set items found yet.")
    st.stop()

st.subheader(f"Gold-set report card (n={gold['n']}, human-verified)")
st.caption("These items were hand-labeled and cross-verified by a human reviewer — the only real "
          "ground truth for CATEGORY, since category has no natural proxy anywhere else in the data. "
          "With only 40 items, treat per-category rates as directional, not statistically precise.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Category accuracy", f"{gold['category_accuracy'] * 100:.1f}%")
c2.metric("Sentiment accuracy", f"{gold['sentiment_accuracy'] * 100:.1f}%")
c3.metric("Urgency exact match", f"{gold['urgency_exact_match_rate'] * 100:.1f}%",
         help=f"Mean absolute error {gold['urgency_mae']} · within +/-1: "
              f"{gold['urgency_within_1_rate'] * 100:.1f}%")
c4.metric("Mismatches", f"{len(gold['mismatches'])} / {gold['n']}")

st.markdown("**Category confusion matrix** — rows = human label, columns = classifier prediction")
cat_df = pd.DataFrame(gold["category_confusion"]).T[gold["categories"]].reindex(gold["categories"])
st.dataframe(cat_df, use_container_width=True)

st.markdown("**Sentiment confusion matrix**")
sent_df = pd.DataFrame(gold["sentiment_confusion"]).T[gold["sentiments"]].reindex(gold["sentiments"])
st.dataframe(sent_df, use_container_width=True)

st.divider()
st.subheader("Silver-sentiment accuracy at scale")
st.caption("Real Olist star ratings + survey NPS scores auto-label thousands of items (not just the "
          "40 gold ones) — a weaker per-item signal (a score, not a person), but a far larger sample.")
sc1, sc2 = st.columns(2)
sc1.metric("Items with a usable score", f"{silver['n_scored']:,}")
sc2.metric("Sentiment accuracy vs. score",
          f"{silver['accuracy'] * 100:.1f}%" if silver["accuracy"] is not None else "n/a")

st.divider()
st.subheader(f"Mismatches ({len(gold['mismatches'])})")
if not gold["mismatches"]:
    st.success("No mismatches — the classifier matched every gold label.")
for m in gold["mismatches"]:
    if m["gold_category"] != m["pred_category"]:
        header = f"[{m['source_type']}] category: {m['gold_category']} -> {m['pred_category']}"
    else:
        header = f"[{m['source_type']}] category OK — sentiment/urgency differs"
    with st.expander(header):
        st.write(m["snippet"])
        st.write(f"**Gold:** {m['gold_category']} / {m['gold_sentiment']} / urgency {m['gold_urgency']}")
        st.write(f"**Predicted:** {m['pred_category']} / {m['pred_sentiment']} / urgency {m['pred_urgency']}")
        if m["labeler_notes"]:
            st.caption(f"Labeler note: {m['labeler_notes']}")
