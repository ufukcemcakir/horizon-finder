import json
import math
import time
import streamlit as st
import pandas as pd

from .phase1_config import CFG, CURATED_LINKS
from .phase1_styles import apply_styles
from .phase1_utils import (
    _trim, _is_valid_email, resolve_status,
    _score_badge_html, _estimate_tokens, is_allowed_action_type, suggest_keywords_from_profile,
)
from .phase1_validation import Validator, validate_signup_form, validate_login_form
from .phase1_db import (
    init_db, migrate_db, get_existing_topic_ids, save_calls, load_calls_df,
    load_active_calls_df, do_signup, do_login, save_org_profile,
    load_org_profile, add_interested, remove_interested, get_interested_calls,
    load_cached_screening, save_screening_cache, load_cached_analyses, save_analysis,
    add_networking_bookmark, get_networking_bookmarks, db_query,
)
from .phase1_pdf import extract_topic_ids_from_pdf
from .phase1_api import fetch_call_by_topic_id, parse_topic_json
from .phase1_prefilter import prefilter_calls
from .phase1_agents import (
    _rate_limiter, run_screening_agent, run_analysis_agent, groq_contribution_idea, _get_groq_client
)

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG & STYLING
# ═══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Horizon Finder",
    page_icon="HF",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_styles()


def _add_status_label(df: pd.DataFrame) -> pd.DataFrame:
    if "status_label" in df.columns:
        return df
    out = df.copy()
    out["status_label"] = out["status"].map(resolve_status)
    return out


def hero(title: str, subtitle: str, badge: str = "", icon: str = "") -> None:
    badge_html = f'<div class="badge">{badge}</div><br>' if badge else ""
    icon_html = f"{icon} " if icon else ""
    st.markdown(
        f'<div class="page-hero">{badge_html}'
        f'<h1>{icon_html}{title}</h1>'
        f'<p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )


def section(label: str) -> None:
    st.markdown(f'<div class="section-label">{label}</div>', unsafe_allow_html=True)


def tip(text: str) -> None:
    st.markdown(f'<div class="tip-box">{text}</div>', unsafe_allow_html=True)


def metrics_row(items: list[tuple]) -> None:
    cols = st.columns(len(items))
    for col, (val, lbl) in zip(cols, items):
        col.markdown(
            f'<div class="metric-card"><div class="metric-val">{val}</div>'
            f'<div class="metric-lbl">{lbl}</div></div>',
            unsafe_allow_html=True,
        )


def _show_idea(call_row: dict | pd.Series, profile_text: str) -> None:
    with st.spinner("Generating contribution idea..."):
        prompt, idea = groq_contribution_idea(
            call_row if isinstance(call_row, dict) else call_row.to_dict(),
            profile_text,
        )
    with st.expander("Inspect LLM prompt"):
        st.text(prompt)
    st.markdown("### Contribution Idea")
    st.write(idea)


def _get_sidebar_stats(user_id: int) -> tuple[int, int, bool]:
    cache_key = f"_sidebar_stats_{user_id}"
    cached = st.session_state.get(cache_key)
    if cached is not None:
        return cached
    n_calls = (db_query("SELECT COUNT(*) FROM horizon_calls", fetch="one") or (0,))[0]
    n_interested = (db_query(
        "SELECT COUNT(*) FROM user_interested_calls WHERE user_id=?",
        (user_id,), fetch="one",
    ) or (0,))[0]
    has_profile = bool(load_org_profile(user_id))
    result = (n_calls, n_interested, has_profile)
    st.session_state[cache_key] = result
    return result


def validate_session() -> None:
    if st.session_state.get("_session_validated"):
        return
    user = st.session_state.get("user")
    if user and not db_query("SELECT id FROM users WHERE id=?", (user.get("id"),), fetch="one"):
        st.session_state.user = None
    st.session_state["_session_validated"] = True


def require_login() -> None:
    if not st.session_state.get("user"):
        st.warning("Please sign in first.")
        st.stop()


def page_auth() -> None:
    """User authentication page (signup/login)."""
    st.markdown('<div class="auth-wrapper">', unsafe_allow_html=True)
    st.markdown(
        '<div class="auth-card"><div class="auth-logo">Horizon Finder</div>'
        '<div class="auth-sub">AI-powered Horizon Europe call intelligence</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    tab_signup, tab_login = st.tabs(["Create Account", "Sign In"])
    
    with tab_signup:
        with st.form("signup"):
            email = st.text_input("Email address")
            name = st.text_input("Full name")
            pw = st.text_input("Password", type="password")
            pw2 = st.text_input("Confirm password", type="password")
            ok = st.form_submit_button("Create Account")
        if ok:
            is_valid, error = validate_signup_form(email, name, pw, pw2)
            if not is_valid:
                st.error(error)
            elif do_signup(email, name, pw):
                st.success("Account created. Switch to Sign In.")
            else:
                st.error("An account with this email already exists.")
    
    with tab_login:
        with st.form("login"):
            email = st.text_input("Email address", key="login_email")
            pw = st.text_input("Password", type="password", key="login_pw")
            ok = st.form_submit_button("Sign In")
        if ok:
            is_valid, error = validate_login_form(email, pw)
            if not is_valid:
                st.error(error)
            else:
                user = do_login(email, pw)
                if user:
                    st.session_state.user = {"id": user[0], "email": user[1], "name": user[2]}
                    st.success(f"Welcome back, {user[2]}!")
                    time.sleep(0.5)
                    st.rerun()
                else:
                    st.error("Invalid email or password.")
    
    st.markdown("</div>", unsafe_allow_html=True)


def main() -> None:
    st.session_state.setdefault("user", None)
    validate_session()

    user = st.session_state.get("user")
    if not user:
        page_auth()
        return

    with st.sidebar:
        st.markdown(
            '<div style="text-align:center;padding:1rem 0 0.5rem;">'
            '<span style="font-size:1.1rem;font-weight:700;color:#e8f4fd;">Horizon Finder</span><br>'
            '<span style="font-size:0.72rem;color:#4a6080;letter-spacing:0.05em;">'
            "EU FUNDING INTELLIGENCE</span></div>",
            unsafe_allow_html=True,
        )
        st.divider()

        st.markdown(
            f'<div class="user-badge">'
            f'<div class="user-name">{user["name"]}</div>'
            f'<div class="user-email">{user["email"]}</div>'
            f"</div>",
            unsafe_allow_html=True,
        )

        st.markdown('<div class="section-label">NAVIGATE</div>', unsafe_allow_html=True)
        page = st.radio("nav", list(PAGES.keys()), label_visibility="collapsed", key="_nav_radio")

        st.divider()

        n_calls, n_interested, has_profile = _get_sidebar_stats(user["id"])
        profile_color = "#48bb78" if has_profile else "#fc8181"
        profile_label = "set" if has_profile else "not set"
        st.markdown(
            '<div style="font-size:0.72rem;color:#4a6080;text-transform:uppercase;'
            'letter-spacing:0.08em;margin-bottom:6px;">Quick Stats</div>'
            f'<div style="font-size:0.82rem;color:#7a90a8;">{n_calls} calls stored</div>'
            f'<div style="font-size:0.82rem;color:#7a90a8;">{n_interested} shortlisted</div>'
            f'<div style="font-size:0.82rem;color:{profile_color};">Profile {profile_label}</div>',
            unsafe_allow_html=True,
        )
        st.divider()

        if st.button("Sign Out", width="stretch"):
            for k in ("user", "screened_df", "agent_recs", "_session_validated"):
                st.session_state.pop(k, None)
            for k in list(st.session_state.keys()):
                if k.startswith("_sidebar_stats_"):
                    st.session_state.pop(k, None)
            st.rerun()

    page_func = PAGES[page]
    page_func()


def page_upload() -> None:
    require_login()
    user = st.session_state.get("user")
    hero("Upload Programmes", "Upload PDF calls or paste Horizon topic IDs to import into your database.")

    cols = st.columns([2, 1])
    with cols[0]:
        uploaded = st.file_uploader("Upload PDF files", type=["pdf"], accept_multiple_files=True)
        pasted = st.text_area("Or paste topic IDs (one per line)")
        if uploaded or pasted:
            found: list[str] = []
            if uploaded:
                for f in uploaded:
                    try:
                        ids = extract_topic_ids_from_pdf(f)
                        found.extend(ids)
                    except Exception:
                        st.warning(f"Could not parse {getattr(f, 'name', 'file')}")
            if pasted:
                for line in pasted.splitlines():
                    t = line.strip().upper()
                    if t:
                        found.append(t)
            found = sorted(set(found))
            st.markdown(f"**Found {len(found)} topic IDs**")
            st.write(found)
            if found:
                if st.button("Fetch calls for found topics"):
                    existing = get_existing_topic_ids()
                    to_fetch = [t for t in found if t not in existing]
                    calls: list[dict] = []
                    with st.spinner(f"Fetching {len(to_fetch)} calls..."):
                        for i, tid in enumerate(to_fetch, 1):
                            item = fetch_call_by_topic_id(tid)
                            if item:
                                calls.append(parse_topic_json(tid, item))
                            time.sleep(0.1)
                    saved, failed = save_calls(calls)
                    st.success(f"Saved {saved} calls. {len(failed)} failures.")
                    if failed:
                        st.write(failed)

    with cols[1]:
        tip("Upload Horizon programme PDFs or paste topic IDs from funding portals.")


def page_discover() -> None:
    require_login()
    user = st.session_state.get("user")
    hero("Discover Calls", "Browse active Horizon calls and filter for relevance.")
    df = load_active_calls_df()
    if df.empty:
        st.info("No active calls in the database. Upload or import calls first.")
        return

    profile = load_org_profile(user["id"]) or ""
    kw = st.text_input("Keyword filter (optional)")
    if kw:
        df = df[df.apply(lambda r: kw.lower() in (str(r.get("title", "") + r.get("call_description", "") + r.get("summary", ""))).lower(), axis=1)]

    st.dataframe(_add_status_label(df).head(200))
    sel = st.multiselect("Select calls to mark interested (by topic_id)", df["topic_id"].tolist())
    if sel and st.button("Mark selected as interested"):
        for tid in sel:
            add_interested(user["id"], tid)
        st.success(f"Marked {len(sel)} calls as interested.")


def page_profile() -> None:
    require_login()
    user = st.session_state.get("user")
    hero("My Profile", "Describe your organization to improve AI recommendations.")
    text = load_org_profile(user["id"]) or ""
    with st.form("profile_form"):
        new = st.text_area("Organization profile (used for matching and LLM prompts)", value=text, height=300)
        ok = st.form_submit_button("Save Profile")
    if ok:
        save_org_profile(user["id"], new)
        st.success("Profile saved.")


def page_shortlist() -> None:
    require_login()
    user = st.session_state.get("user")
    hero("My Shortlist", "Calls you've marked as interested.")
    df = get_interested_calls(user["id"])
    if df.empty:
        st.info("You have no shortlisted calls yet.")
        return
    st.dataframe(_add_status_label(df))
    sel = st.multiselect("Remove selected from shortlist", df["topic_id"].tolist())
    if sel and st.button("Remove selected"):
        for tid in sel:
            remove_interested(user["id"], tid)
        st.success(f"Removed {len(sel)} items.")


def page_recommend() -> None:
    require_login()
    user = st.session_state.get("user")
    hero("AI Recommendations", "Use AI to screen and analyze calls for your organization.")
    profile = load_org_profile(user["id"]) or ""
    if not profile:
        st.warning("Please add your organization profile first.")
        return

    df = load_active_calls_df()
    if df.empty:
        st.info("No active calls available.")
        return

    suggested = prefilter_calls(df, profile)
    st.markdown(f"### Top {min(50, len(suggested))} prefiltered calls")
    st.dataframe(_add_status_label(suggested.head(50)))

    n = st.number_input("Max calls to screen with LLM", min_value=1, max_value=50, value=10)
    if st.button("Run screening (LLM)"):
        client = _get_groq_client()
        if not client:
            st.error("GROQ_API_KEY not set. Cannot run LLM screening.")
            return
        with st.spinner("Running screening agent..."):
            tids = run_screening_agent(suggested, profile, client, top_n=n)
        st.success(f"Screening selected {len(tids)} calls.")
        if tids:
            candidates = suggested[suggested["topic_id"].isin(tids)]
            phash = "phash"  # simple placeholder; in original used profile hash
            client = _get_groq_client()
            with st.spinner("Analyzing shortlisted calls..."):
                scored = run_analysis_agent(candidates, profile, phash, client)
            st.dataframe(scored)


def page_networking() -> None:
    require_login()
    user = st.session_state.get("user")
    hero("Networking", "Save events and contacts for follow-up.")
    with st.form("bookmark"):
        title = st.text_input("Title")
        link = st.text_input("Link/URL")
        event_date = st.date_input("Event date")
        notes = st.text_area("Notes")
        ok = st.form_submit_button("Save")
    if ok:
        add_networking_bookmark(user["id"], title, link, str(event_date), notes)
        st.success("Bookmark saved.")
    rows = get_networking_bookmarks(user["id"])
    if rows:
        st.table(rows)


PAGES: dict[str, callable] = {
    "My Profile": page_profile,
    "Upload Programmes": page_upload,
    "Discover Calls": page_discover,
    "My Shortlist": page_shortlist,
    "AI Recommendations": page_recommend,
    "Networking": page_networking,
}
