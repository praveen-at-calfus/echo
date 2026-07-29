"""echo dashboard - the router entrypoint for the Streamlit app.

Plain English: this is the single front door. Before login it shows the branded
landing screen. After login it hands off to role-appropriate pages via
st.navigation: company staff get the analytics pages in a sidebar (plus a health
status box); feedback users get one "Feed a feedback" page with a top-right
logout and no sidebar at all. Page config, auth, and the sidebar are all decided
here, once, so the individual view scripts stay pure content.

Run: ``python -m echo.frontend`` (or ``streamlit run app.py`` from this directory).
"""

from __future__ import annotations

import common
import streamlit as st

st.set_page_config(page_title="echo", layout="wide")

user = common.current_user()

# ---- Not logged in: the landing / login screen (no sidebar, no header). ----
if user is None:
    common.render_landing()
    st.stop()

# ---- Feedback users (gen_pop): a single page, no sidebar, top-right logout. ----
if user["role"] != common.COMPANY:
    common.hide_sidebar()
    common.render_topright_logout()
    page = st.navigation(
        [st.Page("views/live_feedback.py", title="Feed a feedback")],
        position="hidden",
    )
    page.run()
    st.stop()

# ---- Company users: analytics pages in the sidebar, plus a status box. ----
page = st.navigation([
    st.Page("views/overview.py", title="Overview", default=True),
    st.Page("views/urgent_queue.py", title="Urgent Queue"),
    st.Page("views/themes.py", title="Themes"),
    st.Page("views/weekly_summary.py", title="Weekly Summary"),
    st.Page("views/ask_echo.py", title="Ask echo"),
    st.Page("views/model_evaluation.py", title="Model Evaluation"),
    st.Page("views/user_analytics.py", title="User Analytics"),
])
common.render_account_and_health()
page.run()
