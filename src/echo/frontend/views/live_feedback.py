"""Feed a feedback - the customer-facing submission page (gen_pop only).

Runs via st.navigation from app.py. Each submission is classified, embedded, and
money-attached on the backend (the same pipeline the batch stages use), but the
customer only ever sees a thank-you confirmation, never the internal analysis.

Deliberately built from plain widgets (not st.form) so the rating field can
react the moment the feedback type changes: reviews and surveys carry a rating,
support tickets do not.
"""

from __future__ import annotations

import api_client
import streamlit as st

# Human-readable labels for the dropdowns; the values sent to the API stay the
# machine enums the backend expects.
SOURCE_LABELS = {"review": "Review", "ticket": "Support ticket", "survey": "Survey"}
FULFILLMENT_LABELS = {
    None: "Not sure",
    "on_time_delivered": "Delivered on time",
    "late_delivered": "Delivered late",
    "shipped_not_delivered": "Shipped but never arrived",
    "unavailable": "Item was unavailable",
    "canceled": "Order was canceled",
    "other": "Something else",
}

_STAR_FILLED, _STAR_EMPTY = "★", "☆"  # a deliberate exception to the
# no-decorative-symbols house style: a clickable star rating is the standard
# e-commerce review control, not a decorative emoji.
_STAR_CSS = """
<style>
/* Streamlit adds a "st-key-<key>" class to a keyed widget's wrapper div, so
   this substring match reaches every review_star_rating_star_N button. */
[class*="st-key-review_star_rating_star_"] button {
    font-size: 1.6rem; line-height: 1; padding: 0.1rem 0; border: none; background: transparent;
    color: #c9a227;
}
[class*="st-key-review_star_rating_star_"] button:hover {
    background: transparent; color: #a5811d;
}
</style>
"""


def _star_rating_input(label: str, key: str) -> int:
    """Five clickable stars. Clicking the Nth star sets the rating to N (0 = not
    yet chosen). Mirrors a real e-commerce review widget rather than a slider."""
    st.markdown(_STAR_CSS, unsafe_allow_html=True)
    st.session_state.setdefault(key, 0)
    current = st.session_state[key]

    st.write(label)
    cols = st.columns(5)
    clicked = None
    for i, col in enumerate(cols, start=1):
        glyph = _STAR_FILLED if i <= current else _STAR_EMPTY
        with col:
            if st.button(glyph, key=f"{key}_star_{i}", use_container_width=True):
                clicked = i
    if clicked is not None and clicked != current:
        # Save the new rating, then force a rerun so the page redraws with the
        # correct stars filled in immediately after the click.
        st.session_state[key] = clicked
        st.rerun()
    return current

st.title("Feed a feedback")
st.caption("Tell us about your experience. Your feedback goes straight to the team that can act on it.")

# Submission is classified on the backend, which needs an LLM key configured.
try:
    _health = api_client.health()
except Exception:  # noqa: BLE001
    _health = {"llm": False}
if not _health.get("llm"):
    st.warning("Feedback submission is temporarily unavailable. Please try again later.")
    st.stop()

# --- After a successful submit: show only a thank-you + a way to start over. ---
if st.session_state.get("feedback_submitted"):
    st.success("Your feedback has been submitted. Thank you.")
    st.write("Our team will review it shortly.")
    if st.button("Submit another feedback", type="primary"):
        st.session_state.feedback_submitted = False
        st.rerun()

# --- Otherwise: the submission form. ---
else:
    st.subheader("Share your feedback")
    text = st.text_area("Your feedback", height=150,
                        placeholder="Tell us what happened, what worked, or what did not.")
    source_type = st.selectbox("Type of feedback", ["review", "ticket", "survey"],
                               format_func=lambda v: SOURCE_LABELS[v])

    # Reviews and surveys carry a rating; support tickets do not.
    source_score = None
    source_scale = None
    if source_type == "review":
        source_scale = "star_1_5"
        source_score = _star_rating_input("Your rating", key="review_star_rating")
    elif source_type == "survey":
        source_scale = "nps_0_10"
        source_score = st.number_input("How likely are you to recommend us? (0 to 10)",
                                       min_value=0, max_value=10, value=8, step=1)
    else:
        st.caption("Support tickets do not carry a rating.")

    with st.expander("Order details (optional)"):
        col1, col2 = st.columns(2)
        with col1:
            order_value = st.number_input("Order value (R$)", min_value=0.0, value=0.0, step=10.0)
        with col2:
            refund_amount = st.number_input("Refund or disputed amount (R$)", min_value=0.0, value=0.0, step=10.0)
        fulfillment_outcome = st.selectbox(
            "What happened to your order?",
            [None, "on_time_delivered", "late_delivered", "shipped_not_delivered",
             "unavailable", "canceled", "other"],
            format_func=lambda v: FULFILLMENT_LABELS[v])

    if st.button("Submit feedback", type="primary"):
        if not text.strip():
            st.error("Please enter your feedback first.")
            st.stop()
        if source_type == "review" and not source_score:
            st.error("Please select a star rating.")
            st.stop()
        with st.spinner("Submitting your feedback..."):
            try:
                api_client.submit_feedback(
                    text=text, source_type=source_type,
                    source_score=(float(source_score) if source_scale else None),
                    source_scale=source_scale,
                    order_value=(order_value or None), refund_amount=(refund_amount or None),
                    fulfillment_outcome=fulfillment_outcome)
            except Exception:  # noqa: BLE001
                st.error("Sorry, something went wrong submitting your feedback. Please try again.")
                st.stop()
        st.session_state.review_star_rating = 0  # reset the stars for the next submission
        st.session_state.feedback_submitted = True
        st.rerun()

# --- The customer's own past submissions: their words + date, no analysis. ---
st.divider()
st.subheader("Your submissions")
try:
    mine = api_client.list_feedback(limit=50)
except Exception:  # noqa: BLE001
    mine = None
if mine is None:
    st.caption("Could not load your submissions right now.")
elif mine["total"] == 0:
    st.caption("You haven't submitted any feedback yet.")
else:
    st.caption(f"{mine['total']} submission(s).")
    for it in mine["items"]:
        snippet = it["text"][:160] + ("..." if len(it["text"]) > 160 else "")
        date = (it.get("created_at") or "")[:10]
        st.markdown(f"- {snippet}" + (f"  \n  _{date}_" if date else ""))
