"""Shared UI: authentication (landing screen, login/sign-up, role-aware sidebar
and logout) plus the Direct-Exposure / Retention-Risk metric row reused across
the analytics views.

Plain English: this file owns everything about "who is looking at echo" - the
branded sign-in screen, storing the login token for the session, showing the
right chrome per role (company sees a status sidebar; feedback users see just a
logout), and a couple of small shared widgets. No emojis or em dashes render
here on purpose - that's a house style rule for the UI.
"""

from __future__ import annotations

import re

import api_client
import streamlit as st

COMPANY = "company"
GEN_POP = "gen_pop"

# Mirrors the server-side policy in echo.api.schemas.RegisterIn (kept in sync by
# hand - this is only a fast client-side check; the backend is authoritative).
PASSWORD_MIN_LENGTH = 8
PASSWORD_HINT = ("At least 8 characters, with an uppercase letter, a lowercase letter, "
                  "a number, and a special character.")


def _password_issues(password: str) -> list[str]:
    """Check a password against the policy and return a list of what's missing (empty list if it passes)."""
    missing = []
    if len(password) < PASSWORD_MIN_LENGTH:
        missing.append(f"at least {PASSWORD_MIN_LENGTH} characters")
    if not re.search(r"[A-Z]", password):
        missing.append("an uppercase letter")
    if not re.search(r"[a-z]", password):
        missing.append("a lowercase letter")
    if not re.search(r"\d", password):
        missing.append("a number")
    if not re.search(r"[^A-Za-z0-9]", password):
        missing.append("a special character")
    return missing


# --------------------------------------------------------------------------- #
# Session helpers
# --------------------------------------------------------------------------- #
def current_user() -> dict | None:
    """The logged-in user ({token, role, email}), or None."""
    return st.session_state.get("auth_user")


def logout() -> None:
    """Clear the session and return to the landing screen."""
    st.session_state.pop("auth_user", None)
    st.session_state["auth_view"] = "landing"
    st.rerun()


def _store_and_rerun(token_resp: dict, email: str) -> None:
    """Save the freshly logged-in user into the session and reload the page so the app picks it up."""
    st.session_state["auth_user"] = {
        "token": token_resp["access_token"], "role": token_resp["role"], "email": email,
    }
    st.session_state.pop("auth_view", None)
    st.rerun()


# --------------------------------------------------------------------------- #
# Landing + login screen
# --------------------------------------------------------------------------- #
_LANDING_CSS = """
<style>
/* Landing chrome: hide the sidebar + Streamlit header, paint a glassy black. */
[data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="stHeader"] {
    display: none !important;
}
.stApp {
    background:
        radial-gradient(1200px 600px at 50% -10%, rgba(120,140,180,0.18), transparent 60%),
        radial-gradient(900px 500px at 50% 120%, rgba(90,110,150,0.12), transparent 60%),
        #05070c;
}
.block-container { padding-top: 7vh; max-width: 900px; }

.echo-stage { display: flex; flex-direction: column; align-items: center; gap: 2.2rem; }
.echo-mark { display: flex; align-items: center; justify-content: center; width: 100%; }
.echo-word {
    color: #f5f7fb; font-weight: 700; letter-spacing: 0.18em;
    font-size: clamp(56px, 12vw, 150px); line-height: 1;
    padding: 0 0.35em; text-shadow: 0 0 28px rgba(190,210,255,0.35);
}
.arcs { display: flex; align-items: center; height: 1.4em; }
.arcs-left { flex-direction: row; justify-content: flex-end; }
.arcs-right { flex-direction: row; justify-content: flex-start; }
.arc {
    height: 100%;
    border: 0 solid rgba(255,255,255,0.95);
    animation: echo-pulse 3.2s ease-out infinite;
}
.arc-left  { border-left-width: 2px;  border-top-left-radius: 100%;  border-bottom-left-radius: 100%; }
.arc-right { border-right-width: 2px; border-top-right-radius: 100%; border-bottom-right-radius: 100%; }
@keyframes echo-pulse {
    0%   { opacity: 0; transform: scaleX(0.7); }
    18%  { opacity: 1; }
    100% { opacity: 0.15; transform: scaleX(1.15); }
}

/* Dark-friendly inputs/buttons for the sign-in card. */
.stApp [data-testid="stTextInput"] label,
.stApp [data-testid="stRadio"] label p,
.stApp [data-testid="stCaptionContainer"] { color: #d7deea !important; }
.stApp .stButton > button {
    background: rgba(255,255,255,0.08); color: #f5f7fb;
    border: 1px solid rgba(255,255,255,0.25); border-radius: 10px;
    padding: 0.5rem 2.2rem; font-weight: 600;
}
.stApp .stButton > button:hover { border-color: #9fb4dd; color: #fff; }
</style>
"""


def _arc_html() -> str:
    """Build the left/right side arcs: near the word solid + tight, outward
    blurrier + fainter (the solid-to-blurry, expanding echo look)."""
    n = 5
    left, right = [], []
    for i in range(n):  # i=0 nearest the word (solid), i=n-1 outermost (blurry)
        w = 0.5 + 1.1 * i           # em: arcs widen outward
        blur = 0.6 * i              # px: outer arcs blur out
        opacity = 1.0 - 0.16 * i
        delay = 0.28 * i
        style = (f"width:{w:.2f}em;filter:blur({blur:.1f}px);opacity:{opacity:.2f};"
                 f"animation-delay:{delay:.2f}s;")
        left.append(f'<span class="arc arc-left" style="{style}"></span>')
        right.append(f'<span class="arc arc-right" style="{style}"></span>')
    # left arcs read blurry->solid toward the word, so reverse the left stack
    return (f'<span class="arcs arcs-left">{"".join(reversed(left))}</span>'
            f'<span class="echo-word">echo</span>'
            f'<span class="arcs arcs-right">{"".join(right)}</span>')


def render_landing() -> None:
    """The glassy-black landing screen: the echo wordmark with side arc waves and
    a Login button that reveals the login / create-account form."""
    st.markdown(_LANDING_CSS, unsafe_allow_html=True)
    view = st.session_state.get("auth_view", "landing")

    st.markdown(f'<div class="echo-stage"><div class="echo-mark">{_arc_html()}</div></div>',
                unsafe_allow_html=True)
    st.write("")

    if view == "landing":
        _, mid, _ = st.columns([2, 1, 2])
        with mid:
            if st.button("Login", use_container_width=True, type="primary"):
                st.session_state["auth_view"] = "form"
                st.rerun()
        st.markdown(
            "<p style='text-align:center;color:#8792a6;margin-top:1.2rem;'>"
            "Customer-feedback intelligence for e-commerce.</p>", unsafe_allow_html=True)
        return

    # view == "form"
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        _render_auth_form()
        if st.button("Back", use_container_width=True):
            st.session_state["auth_view"] = "landing"
            st.rerun()


def _render_auth_form() -> None:
    """Render the log-in / sign-up form (mode toggle plus the matching fields and submit handling)."""
    # A mode toggle (not st.tabs) because a successful sign-up needs to switch
    # the view back to "Log in" programmatically, which tabs cannot do. The
    # switch is deferred to a flag checked here, BEFORE the radio widget below
    # is instantiated - Streamlit forbids writing to a widget's session_state
    # key after that widget has already been created in the same run.
    if st.session_state.pop("_force_login_mode", False):
        st.session_state["auth_mode"] = "Log in"

    mode = st.radio("Mode", ["Log in", "Sign up"], horizontal=True,
                    label_visibility="collapsed", key="auth_mode")

    if mode == "Log in":
        if st.session_state.pop("signup_success", False):
            st.success("Account created. Please log in.")
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Log in", use_container_width=True, type="primary"):
                try:
                    resp = api_client.login(email.strip(), password)
                except api_client.ApiError as e:
                    st.error(str(e))
                except Exception as e:  # noqa: BLE001
                    st.error(f"Could not reach the echo API at {api_client.BASE_URL}: {e}")
                else:
                    _store_and_rerun(resp, email.strip())
        return

    # mode == "Sign up"
    st.caption("Public sign-up creates a feedback account. Company accounts are provisioned by staff.")
    with st.form("register_form"):
        email = st.text_input("Email", key="reg_email")
        password = st.text_input("Password", type="password", key="reg_pw")
        confirm = st.text_input("Confirm password", type="password", key="reg_pw_confirm")
        st.caption(PASSWORD_HINT)
        full_name = st.text_input("Name (optional)", key="reg_name")
        if st.form_submit_button("Create account", use_container_width=True, type="primary"):
            issues = _password_issues(password)
            if password != confirm:
                st.error("Passwords do not match.")
            elif issues:
                st.error("Password must contain " + ", ".join(issues) + ".")
            else:
                try:
                    api_client.register(email.strip(), password, full_name.strip() or None)
                except api_client.ApiError as e:
                    st.error(str(e))
                except Exception as e:  # noqa: BLE001
                    st.error(f"Could not reach the echo API at {api_client.BASE_URL}: {e}")
                else:
                    st.session_state["_force_login_mode"] = True
                    st.session_state["signup_success"] = True
                    st.rerun()


# --------------------------------------------------------------------------- #
# Post-login chrome
# --------------------------------------------------------------------------- #
_HIDE_SIDEBAR_CSS = """
<style>[data-testid="stSidebar"], [data-testid="stSidebarNav"] { display: none !important; }</style>
"""


def hide_sidebar() -> None:
    """Gen-pop users get no sidebar at all."""
    st.markdown(_HIDE_SIDEBAR_CSS, unsafe_allow_html=True)


def render_topright_logout() -> None:
    """A right-aligned logout for the gen-pop single-page view (no sidebar)."""
    user = current_user()
    _, right = st.columns([6, 1])
    with right:
        if st.button("Log out", use_container_width=True):
            logout()
    if user:
        st.caption(f"Signed in as {user['email']}")


def render_account_and_health() -> None:
    """Company sidebar: the account box, a logout, and DB/LLM status as plain text."""
    user = current_user()
    if user:
        st.sidebar.markdown(f"**{user['email']}**")
        st.sidebar.caption("Company account")
        if st.sidebar.button("Log out", use_container_width=True):
            logout()
        st.sidebar.divider()

    try:
        h = api_client.health()
    except Exception as e:  # noqa: BLE001
        st.sidebar.error(f"API unreachable at {api_client.BASE_URL}: {e}")
        return
    st.sidebar.markdown("### System status")
    st.sidebar.write("Database: " + ("online" if h["db"] else "offline"))
    st.sidebar.write("Live LLM features: " + ("on" if h["llm"] else "off (no API key)"))
    st.sidebar.caption(f"build: {h.get('build_id') or 'n/a'}")


# --------------------------------------------------------------------------- #
# Shared money metric row (used by the analytics views)
# --------------------------------------------------------------------------- #
def _compact(n: float) -> str:
    """R$ figure, abbreviated to fit a narrow metric tile (exact value goes in `help`)."""
    n = float(n)
    if abs(n) >= 1_000_000:
        return f"R$ {n / 1_000_000:,.1f}M"
    if abs(n) >= 1_000:
        return f"R$ {n / 1_000:,.0f}k"
    return f"R$ {n:,.0f}"


def money_metrics(direct_exposure: float, retention: dict, revenue_at_risk: float | None = None) -> None:
    """Direct Exposure (deterministic) + Retention Risk (modeled low/base/high) as metric tiles."""
    cols = st.columns(4 if revenue_at_risk is not None else 3)
    cols[0].metric("Direct Exposure", _compact(direct_exposure),
                   help=f"Deterministic: actual $ from real fields (refunds, disputes, lost orders). "
                        f"Exact: R$ {direct_exposure:,.2f}")
    cols[1].metric("Retention Risk (base)", _compact(retention["base"]),
                   help=f"Modeled estimate, not measured. See the range for sensitivity. "
                        f"Exact: R$ {retention['base']:,.2f}")
    low, high = _compact(retention["low"]), _compact(retention["high"])
    cols[2].metric("Retention range (low-high)", f"{low} to {high.replace('R$ ', '')}")
    if revenue_at_risk is not None:
        cols[3].metric("Revenue at risk", _compact(revenue_at_risk),
                       help=f"Direct Exposure + Retention Risk (base). Exact: R$ {revenue_at_risk:,.2f}")
