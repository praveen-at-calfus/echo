"""User Analytics - per-user submission behavior for company/admin accounts.

Plain English: who is actually using echo, and what are they telling us? Lists
every registered account, how much feedback they have fed in, their overall
tone, and lets an admin drill into one user's history over time. Runs via
st.navigation from app.py (router owns page config, auth, sidebar).

Scope: only feedback tied to a real account (submitted through "Feed a
feedback") counts here - the 15k batch corpus has no submitter and never
appears, so this page is purely about live user behavior.
"""

from __future__ import annotations

import api_client
import pandas as pd
import streamlit as st
from charts import magnitude_bar, sentiment_split_bar, sentiment_trend
from common import money_metrics

ROLE_LABELS = {"company": "Company", "gen_pop": "Feedback user"}


def _opinion_label(net: float | None) -> str:
    """Turn a net-sentiment score (positive share minus negative share) into a plain label. Returns the label string."""
    if net is None:
        return "No data"
    # A small dead zone around zero (+/-0.15) counts as "Mixed" rather than
    # forcing every user into a strict Positive/Negative bucket on a tiny nudge.
    if net > 0.15:
        return "Positive"
    if net < -0.15:
        return "Negative"
    return "Mixed"


st.title("User Analytics")
st.caption("How registered users are actually using echo: how much feedback they submit, their "
           "overall tone, and how that tone moves over time. Only feedback submitted through an "
           "account is counted here - the original 15,000-item corpus has no owner and is excluded.")

try:
    data = api_client.users_analytics()
except Exception as e:  # noqa: BLE001
    st.error(f"Could not reach the echo API: {e}")
    st.stop()

totals = data["totals"]
c1, c2, c3 = st.columns(3)
c1.metric("Registered users", f"{totals['users']:,}")
c2.metric("Users who have submitted feedback", f"{totals['users_with_submissions']:,}")
c3.metric("Total user submissions", f"{totals['submissions']:,}")

st.divider()
st.subheader("All users")
# "Avg urgency" is always formatted as a string (even the "-" for no data) so
# the whole column has one consistent type; mixing real numbers and a "-"
# string in the same column previously broke the table's rendering.
rows = [{
    "Email": u["email"],
    "Role": ROLE_LABELS.get(u["role"], u["role"]),
    "Submissions": u["n_submissions"],
    "Overall opinion": _opinion_label(u["net_sentiment"]),
    "Avg urgency": f"{u['avg_urgency']:.2f}" if u["avg_urgency"] is not None else "-",
    "Last active": (u["last_submission"][:10] if u["last_submission"] else "Never"),
} for u in data["users"]]
st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.divider()
st.subheader("Per-user detail")

active_users = [u for u in data["users"] if u["n_submissions"] > 0]
if not active_users:
    st.caption("No user has submitted feedback yet, so there is nothing to drill into.")
else:
    selected_email = st.selectbox("Choose a user", [u["email"] for u in active_users])
    user = next(u for u in active_users if u["email"] == selected_email)

    k1, k2, k3 = st.columns(3)
    k1.metric("Submissions", user["n_submissions"])
    k2.metric("Avg urgency", user["avg_urgency"])
    k3.metric("Overall opinion", _opinion_label(user["net_sentiment"]),
             help=f"Net sentiment score (positive minus negative share): {user['net_sentiment']}")

    st.markdown("**Money at stake from this user's negative feedback**")
    money_metrics(user["direct_exposure"], user["retention"], user["revenue_at_risk"])

    left, right = st.columns(2)
    with left:
        st.markdown("**Sentiment split**")
        st.plotly_chart(sentiment_split_bar(user["sentiment"]), use_container_width=True, theme=None)
    with right:
        st.markdown("**Category focus**")
        cat_rows = [{"category": k, "count": v} for k, v in user["category_counts"].items()]
        if cat_rows:
            st.plotly_chart(
                magnitude_bar(cat_rows, "category", "count", "Feedback by category", "Items", "Category",
                             horizontal=True),
                use_container_width=True, theme=None)
        else:
            st.caption("No categorized submissions yet.")

    st.markdown("**Opinion over time**")
    if len(user["sentiment_over_time"]) >= 2:
        st.plotly_chart(sentiment_trend(user["sentiment_over_time"]), use_container_width=True, theme=None)
    else:
        st.caption("Not enough weeks of history yet to show a trend.")

    st.markdown("**Submissions**")
    for s in user["submissions"]:
        date = (s["created_at"] or "")[:10]
        header = f"{s['category']} · {s['sentiment']} · urgency {s['urgency']} · {date}"
        with st.expander(header):
            st.write(s["snippet"])
            st.caption(f"item_id: {s['item_id']} · source: {s['source_type']}")
