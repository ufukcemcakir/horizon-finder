"""
Styling and theme constants for Horizon Finder.

Maintains consistent UI/UX across all pages and components.
"""

# Main theme colors
COLOR_PRIMARY = "#63b3ed"          # Bright blue
COLOR_DARK_BG = "#0f1b35"           # Dark blue background
COLOR_DARKER_BG = "#0d1a30"         # Darker background for cards
COLOR_SUCCESS = "#48bb78"           # Green
COLOR_WARNING = "#ed8936"           # Orange
COLOR_MUTED = "#a0aec0"            # Gray

# CSS with proper formatting
CSS_STYLES = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

* { font-family: 'Inter', sans-serif; }

/* Sidebar styling */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0f1b35 0%, #1a2d5a 100%);
    border-right: 1px solid rgba(255,255,255,0.08);
}
[data-testid="stSidebar"] * { color: #e8edf5 !important; }

/* Page container */
.main .block-container {
    padding-top: 2rem;
    max-width: 1200px;
}

/* Hero section */
.page-hero {
    background: linear-gradient(135deg, #0f1b35 0%, #1a3a6e 60%, #0d2d5e 100%);
    border-radius: 16px;
    padding: 2rem 2.5rem;
    margin-bottom: 2rem;
    border: 1px solid rgba(99,179,237,0.2);
    position: relative;
    overflow: hidden;
}
.page-hero h1 {
    color: #e8f4fd;
    margin: 0 0 6px;
    font-size: 1.7rem;
    font-weight: 700;
}
.page-hero p {
    color: #90aecb;
    margin: 0;
    font-size: 0.95rem;
    line-height: 1.6;
}
.page-hero .badge {
    display: inline-block;
    background: rgba(99,179,237,0.15);
    color: #63b3ed;
    border: 1px solid rgba(99,179,237,0.3);
    border-radius: 20px;
    padding: 2px 12px;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin-bottom: 10px;
}

/* Section label */
.section-label {
    font-size: 0.72rem;
    color: #4a6080;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 8px;
    font-weight: 600;
}

/* Tip box */
.tip-box {
    background: rgba(99,179,237,0.08);
    border-left: 4px solid #63b3ed;
    padding: 12px 16px;
    border-radius: 6px;
    color: #90aecb;
    font-size: 0.85rem;
    margin-bottom: 1rem;
}

/* Metrics */
.metric-row {
    display: flex;
    gap: 12px;
    margin-bottom: 1.5rem;
    flex-wrap: wrap;
}
.metric-card {
    flex: 1;
    min-width: 140px;
    background: #0f1b35;
    border: 1px solid rgba(99,179,237,0.18);
    border-radius: 12px;
    padding: 1.1rem 1.3rem;
}
.metric-card .metric-val {
    font-size: 1.8rem;
    font-weight: 700;
    color: #63b3ed;
    line-height: 1;
}
.metric-card .metric-lbl {
    font-size: 0.75rem;
    color: #7a90a8;
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

/* Auth styling */
.auth-wrapper {
    display: flex;
    justify-content: center;
    align-items: center;
    min-height: 60vh;
    padding: 2rem;
}
.auth-card {
    background: #0f1b35;
    border: 1px solid rgba(99,179,237,0.2);
    border-radius: 16px;
    padding: 3rem 2.5rem;
    max-width: 400px;
    text-align: center;
}
.auth-card .auth-logo {
    font-size: 1.8rem;
    font-weight: 700;
    color: #e8f4fd;
    margin-bottom: 8px;
}
.auth-card .auth-sub {
    font-size: 0.85rem;
    color: #7a90a8;
    margin-bottom: 2rem;
}

/* User badge in sidebar */
.user-badge {
    background: rgba(99,179,237,0.1);
    border: 1px solid rgba(99,179,237,0.2);
    border-radius: 12px;
    padding: 12px 16px;
    margin-bottom: 1.5rem;
    text-align: center;
}
.user-badge .user-name {
    font-weight: 600;
    color: #e8f4fd;
    font-size: 0.95rem;
    margin-bottom: 2px;
}
.user-badge .user-email {
    font-size: 0.75rem;
    color: #7a90a8;
}

/* Status pills */
.pill {
    display: inline-block;
    border-radius: 20px;
    padding: 2px 10px;
    font-size: 0.72rem;
    font-weight: 600;
}
.pill-open {
    background: rgba(72,187,120,0.15);
    color: #48bb78;
    border: 1px solid rgba(72,187,120,0.3);
}
.pill-forthcoming {
    background: rgba(237,137,54,0.15);
    color: #ed8936;
    border: 1px solid rgba(237,137,54,0.3);
}
.pill-closed {
    background: rgba(160,174,192,0.15);
    color: #a0aec0;
    border: 1px solid rgba(160,174,192,0.3);
}

/* Data table styling */
[data-testid="stDataFrame"] {
    font-size: 0.85rem;
}
</style>
"""


def apply_styles() -> None:
    """Apply theme CSS to the current Streamlit session."""
    import streamlit as st
    st.markdown(CSS_STYLES, unsafe_allow_html=True)
