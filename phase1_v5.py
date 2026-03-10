# phase1_v5.py — Horizon Europe Phase-1 Intelligence Platform
# Run with: streamlit run phase1_v5.py

import streamlit as st
import pdfplumber
import re, os, json, time, hashlib, sqlite3
import numpy as np
import pandas as pd
import requests
from collections import Counter
from groq import Groq
from sentence_transformers import SentenceTransformer
import faiss
from datetime import datetime

# ── Page Config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Horizon Finder",
    page_icon="🔭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global CSS ────────────────────────────────────────────────────────────────

st.markdown("""
<style>
/* ── Typography & Base ── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0f1b35 0%, #1a2d5a 100%);
    border-right: 1px solid rgba(255,255,255,0.08);
}
[data-testid="stSidebar"] * { color: #e8edf5 !important; }
[data-testid="stSidebar"] .stRadio > label { color: #a0aec0 !important; font-size: 0.78rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 4px; }
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] { gap: 2px; }
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] label {
    background: rgba(255,255,255,0.04);
    border-radius: 8px;
    padding: 8px 12px;
    transition: all 0.2s;
    border: 1px solid transparent;
    color: #c5cfe0 !important;
    font-size: 0.875rem;
}
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] label:hover {
    background: rgba(99,179,237,0.15);
    border-color: rgba(99,179,237,0.3);
    color: #90cdf4 !important;
}

/* ── Main area ── */
.main .block-container { padding-top: 2rem; max-width: 1200px; }

/* ── Page header ── */
.page-hero {
    background: linear-gradient(135deg, #0f1b35 0%, #1a3a6e 60%, #0d2d5e 100%);
    border-radius: 16px;
    padding: 2rem 2.5rem;
    margin-bottom: 2rem;
    border: 1px solid rgba(99,179,237,0.2);
    position: relative;
    overflow: hidden;
}
.page-hero::after {
    content: '';
    position: absolute;
    top: -50%; right: -10%;
    width: 350px; height: 350px;
    background: radial-gradient(circle, rgba(99,179,237,0.08) 0%, transparent 70%);
    pointer-events: none;
}
.page-hero h1 { color: #e8f4fd; margin: 0 0 6px; font-size: 1.7rem; font-weight: 700; }
.page-hero p  { color: #90aecb; margin: 0; font-size: 0.95rem; line-height: 1.6; }
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

/* ── Metric cards ── */
.metric-row { display: flex; gap: 12px; margin-bottom: 1.5rem; flex-wrap: wrap; }
.metric-card {
    flex: 1; min-width: 140px;
    background: #0f1b35;
    border: 1px solid rgba(99,179,237,0.18);
    border-radius: 12px;
    padding: 1.1rem 1.3rem;
}
.metric-card .metric-val { font-size: 1.8rem; font-weight: 700; color: #63b3ed; line-height: 1; }
.metric-card .metric-lbl { font-size: 0.75rem; color: #7a90a8; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.05em; }

/* ── Call cards ── */
.call-card {
    background: #0d1a30;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 10px;
    transition: border-color 0.2s;
}
.call-card:hover { border-color: rgba(99,179,237,0.35); }
.call-card .call-id { font-size: 0.72rem; color: #63b3ed; font-family: monospace; font-weight: 600; margin-bottom: 4px; }
.call-card .call-title { font-size: 0.95rem; font-weight: 600; color: #e2eaf6; margin-bottom: 8px; }
.call-card .call-meta { font-size: 0.78rem; color: #7a90a8; }

/* ── Status pills ── */
.pill { display: inline-block; border-radius: 20px; padding: 2px 10px; font-size: 0.72rem; font-weight: 600; }
.pill-open        { background: rgba(72,187,120,0.15); color: #48bb78; border: 1px solid rgba(72,187,120,0.3); }
.pill-forthcoming { background: rgba(237,137,54,0.15);  color: #ed8936; border: 1px solid rgba(237,137,54,0.3); }
.pill-closed      { background: rgba(160,174,192,0.15); color: #a0aec0; border: 1px solid rgba(160,174,192,0.3); }

/* ── Score badges ── */
.score-badge { display: inline-block; border-radius: 8px; padding: 4px 12px; font-weight: 700; font-size: 0.9rem; }
.score-high   { background: rgba(72,187,120,0.2);  color: #68d391; }
.score-mid    { background: rgba(237,137,54,0.2);  color: #f6ad55; }
.score-low    { background: rgba(245,101,101,0.2); color: #fc8181; }

/* ── Section dividers ── */
.section-label {
    font-size: 0.7rem; font-weight: 700; letter-spacing: 0.1em;
    text-transform: uppercase; color: #4a6080;
    margin: 1.5rem 0 0.5rem; padding-bottom: 6px;
    border-bottom: 1px solid rgba(255,255,255,0.06);
}

/* ── Info/tip boxes ── */
.tip-box {
    background: rgba(99,179,237,0.06);
    border-left: 3px solid #63b3ed;
    border-radius: 0 8px 8px 0;
    padding: 0.75rem 1rem;
    font-size: 0.85rem;
    color: #90aecb;
    margin: 1rem 0;
}

/* ── Step indicators ── */
.step-row { display: flex; gap: 0; margin-bottom: 1.5rem; }
.step-item { flex: 1; text-align: center; position: relative; }
.step-circle {
    width: 32px; height: 32px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 6px;
    font-size: 0.8rem; font-weight: 700;
}
.step-active   .step-circle { background: #63b3ed; color: #0f1b35; }
.step-done     .step-circle { background: #48bb78; color: #0f1b35; }
.step-inactive .step-circle { background: rgba(255,255,255,0.08); color: #4a6080; }
.step-label { font-size: 0.7rem; color: #4a6080; }
.step-active .step-label { color: #63b3ed; }
.step-item::before {
    content: '';
    position: absolute; top: 16px; left: -50%; right: 50%;
    height: 2px; background: rgba(255,255,255,0.06);
    z-index: 0;
}
.step-item:first-child::before { display: none; }

/* ── Auth cards ── */
.auth-wrapper { max-width: 420px; margin: 3rem auto; }
.auth-card {
    background: #0d1a30;
    border: 1px solid rgba(99,179,237,0.18);
    border-radius: 16px;
    padding: 2rem 2.5rem;
}
.auth-logo { text-align: center; margin-bottom: 1.5rem; font-size: 2.5rem; }
.auth-title { text-align: center; font-size: 1.3rem; font-weight: 700; color: #e8f4fd; margin-bottom: 0.3rem; }
.auth-sub   { text-align: center; font-size: 0.85rem; color: #7a90a8; margin-bottom: 1.5rem; }

/* ── Buttons ── */
div.stButton > button {
    border-radius: 8px;
    font-weight: 600;
    font-size: 0.875rem;
    transition: all 0.2s;
}
div.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #2b6cb0, #3182ce);
    border: none;
    color: white;
}
div.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #3182ce, #4299e1);
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(49,130,206,0.4);
}

/* ── Expanders ── */
.streamlit-expanderHeader {
    background: rgba(255,255,255,0.03) !important;
    border-radius: 8px !important;
    font-size: 0.88rem !important;
}

/* ── Dataframe ── */
[data-testid="stDataFrame"] { border-radius: 10px; overflow: hidden; }

/* ── Forms ── */
[data-testid="stForm"] {
    background: #0d1a30;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 12px;
    padding: 1.5rem;
}

/* ── Sidebar user badge ── */
.user-badge {
    background: rgba(99,179,237,0.1);
    border: 1px solid rgba(99,179,237,0.2);
    border-radius: 10px;
    padding: 0.6rem 0.9rem;
    margin-bottom: 1rem;
    font-size: 0.82rem;
}
.user-badge .user-name { font-weight: 600; color: #90cdf4; }
.user-badge .user-email { color: #718096; font-size: 0.75rem; }
</style>
""", unsafe_allow_html=True)

# ── Constants ─────────────────────────────────────────────────────────────────

DB_PATH        = "horizon.db"
API_KEY        = "SEDIA"
FT_SEARCH_BASE = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"

# Only these two action types are allowed (filter requirement)
ALLOWED_ACTION_TYPES = {"IA", "RIA", "HORIZON-IA", "HORIZON-RIA",
                         "Innovation Action", "Research and Innovation Action",
                         "HORIZON Innovation Actions",
                         "HORIZON Research and Innovation Actions"}

CALLS_COLS = [
    "topic_id", "title", "call_description", "summary", "status", "deadline",
    "opening_date", "type_of_action", "programme_period", "url", "raw_json",
]
_CALLS_PH = ",".join("?" * len(CALLS_COLS))

STATUS_MAP = {
    "31094501": "Open", "31094502": "Forthcoming", "31094503": "Closed",
    "open": "Open", "forthcoming": "Forthcoming", "closed": "Closed",
}

CURATED_LINKS = [
    ("🌐 Funding & Tenders Portal", "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/browse-by-programme"),
    ("💻 Ideal-ist (ICT/Digital)",  "https://www.ideal-ist.eu/"),
    ("🤝 Enterprise Europe Network", "https://een.ec.europa.eu/"),
    ("🔍 EEN Partnering",           "https://een.ec.europa.eu/partnering-opportunities"),
    ("📋 Horizon NCP Networks",     "https://www.ncpportal.eu/"),
    ("🚀 EUREKA Network",           "https://www.eurekanetwork.org/"),
]

NAV_ICONS = {
    "📥  Upload Programmes":  "upload",
    "🔍  Discover Calls":     "discover",
    "⭐  My Shortlist":       "interested",
    "🤖  AI Recommendations": "recommend",
    "🏢  My Profile":         "profile",
    "🤝  Networking":         "networking",
}

# ── Model ─────────────────────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def load_model():
    return SentenceTransformer("sentence-transformers/all-mpnet-base-v2")

model = load_model()

# ── Database ──────────────────────────────────────────────────────────────────

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def db_query(sql, params=(), *, fetch="all", write=False):
    conn = get_conn()
    cur  = conn.execute(sql, params)
    if write:
        conn.commit()
        conn.close()
        return None
    result = cur.fetchall() if fetch == "all" else cur.fetchone()
    conn.close()
    return result

def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE, name TEXT, password_hash TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS org_profile (
        user_id INTEGER UNIQUE, profile_text TEXT, embedding BLOB, updated_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS horizon_calls (
        topic_id TEXT PRIMARY KEY, title TEXT, call_description TEXT,
        summary TEXT, status TEXT, deadline TEXT, opening_date TEXT,
        type_of_action TEXT, programme_period TEXT, url TEXT, raw_json TEXT
    );
    CREATE TABLE IF NOT EXISTS user_interested_calls (
        user_id INTEGER, topic_id TEXT, created_at TEXT,
        PRIMARY KEY (user_id, topic_id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS networking_bookmarks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, title TEXT, link TEXT, event_date TEXT,
        notes TEXT, created_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS analysis_cache (
        profile_hash TEXT, topic_id TEXT,
        score INTEGER, verdict TEXT, strengths TEXT, gaps TEXT, created_at TEXT,
        PRIMARY KEY (profile_hash, topic_id)
    );
    """)
    conn.commit()
    conn.close()

def migrate_db():
    conn = get_conn()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(horizon_calls)").fetchall()}

    if "scope" in cols and "call_description" not in cols:
        conn.execute("ALTER TABLE horizon_calls ADD COLUMN call_description TEXT DEFAULT ''")
        conn.execute("""
            UPDATE horizon_calls SET call_description =
                COALESCE(NULLIF(scope, ''), '')
                || CASE WHEN scope != '' AND expected_outcomes != ''
                        THEN char(10)||char(10) ELSE '' END
                || COALESCE(NULLIF(expected_outcomes, ''), '')
        """)
        conn.commit()

    rows = conn.execute(
        "SELECT topic_id, raw_json FROM horizon_calls "
        "WHERE call_description IS NULL OR call_description = ''"
    ).fetchall()
    for topic_id, raw_json_str in rows:
        if not raw_json_str:
            continue
        try:
            raw = json.loads(raw_json_str)
        except Exception:
            continue
        md   = raw.get("metadata") or {}
        desc = (
            get_description_byte(raw)
            or raw.get("scope") or md.get("scope")
            or raw.get("expectedOutcomes") or md.get("expectedOutcomes")
            or ""
        )
        if desc:
            conn.execute("UPDATE horizon_calls SET call_description=? WHERE topic_id=?", (desc, topic_id))

    conn.commit()
    conn.close()

# ── Utilities ─────────────────────────────────────────────────────────────────

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()

def now_iso() -> str:
    return datetime.utcnow().isoformat()

def embed_text(text: str) -> np.ndarray:
    return model.encode([text], convert_to_numpy=True, normalize_embeddings=True)[0]

def resolve_status(s) -> str:
    s = str(s)
    return STATUS_MAP.get(s, STATUS_MAP.get(s.lower(), s))

def is_allowed_action_type(toa: str) -> bool:
    """Return True if the type_of_action is IA or RIA."""
    if not toa:
        return False
    toa_upper = toa.upper()
    for allowed in ALLOWED_ACTION_TYPES:
        if allowed.upper() in toa_upper:
            return True
    # Short-code check: split by ; and check each segment
    for segment in re.split(r"[;,]", toa):
        seg = segment.strip().upper()
        if seg in {"IA", "RIA"}:
            return True
    return False

def status_pill(status_label: str) -> str:
    cls = {"Open": "pill-open", "Forthcoming": "pill-forthcoming"}.get(status_label, "pill-closed")
    return f'<span class="pill {cls}">{status_label}</span>'

def score_badge(score: int) -> str:
    cls = "score-high" if score >= 70 else "score-mid" if score >= 45 else "score-low"
    icon = "🟢" if score >= 70 else "🟡" if score >= 45 else "🔴"
    return f'<span class="score-badge {cls}">{icon} {score}/100</span>'

# ── PDF extraction ────────────────────────────────────────────────────────────

_RE_TOPIC = re.compile(
    r"\bHORIZON-[A-Z0-9]+-\d{4}-[\w-]+-\d{1,2}-\d{1,2}\b"
    r"|\bHORIZON-[A-Z0-9]+-\d{4}-[\w-]+-\d{2}\b",
    re.IGNORECASE,
)

def extract_topic_ids_from_pdf(uploaded_file) -> list:
    found = set()

    def _scan(text: str):
        text = re.sub(r"[\u00ad\u200b]", "", text)
        text = re.sub(r"-\n\s*", "-", text)
        text = re.sub(r"\s+", " ", text)
        for m in _RE_TOPIC.findall(text):
            m_up = m.upper()
            if m_up.count("-") >= 4:
                found.add(m_up)

    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            _scan(page.extract_text() or "")
            try:
                _scan(" ".join(
                    w["text"] for w in
                    page.extract_words(keep_blank_chars=False, use_text_flow=True)
                ))
            except Exception:
                pass
            try:
                for table in page.extract_tables():
                    for row in (table or []):
                        for cell in (row or []):
                            if cell:
                                _scan(str(cell))
            except Exception:
                pass

    try:
        uploaded_file.seek(0)
        _scan(uploaded_file.read().decode("latin-1", errors="replace"))
        uploaded_file.seek(0)
    except Exception:
        pass

    return sorted(found)

# ── API helpers ───────────────────────────────────────────────────────────────

def _pick_first(v):
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    return v

def _sg(dct, key, default=None):
    return dct.get(key, default) if isinstance(dct, dict) else default

def _pf(dct, key):
    return _pick_first(_sg(dct, key))

def _strip_html(html: str) -> str:
    from html.parser import HTMLParser
    class _P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
        def handle_data(self, d):
            if d.strip():
                self.parts.append(d.strip())
    p = _P()
    p.feed(html)
    return "\n".join(p.parts).strip()

def get_description_byte(result_item: dict) -> str:
    md  = _sg(result_item, "metadata") or {}
    raw = _sg(md, "descriptionByte") or ""
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    raw = str(raw).strip()
    return _strip_html(raw) if raw else ""

_api_session = requests.Session()
_api_session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

def fetch_call_by_topic_id(topic_id: str):
    try:
        r = _api_session.post(
            f'{FT_SEARCH_BASE}?apiKey={API_KEY}&text="{topic_id}"',
            json={"pageNumber": 1, "pageSize": 1},
            timeout=20,
        )
        if r.status_code == 200:
            results = r.json().get("results") or []
            if results:
                return results[0]
    except Exception:
        pass
    try:
        r = _api_session.post(
            f"{FT_SEARCH_BASE}?apiKey={API_KEY}",
            json={"pageNumber": 1, "pageSize": 5, "text": topic_id},
            timeout=20,
        )
        if r.status_code == 200:
            return _pick_best_result(r.json().get("results") or [], topic_id)
    except Exception:
        pass
    return None

def _pick_best_result(results: list, topic_id: str):
    if not results:
        return None
    tid = topic_id.upper()
    for r in results:
        md = _sg(r, "metadata", {})
        if any(
            c and str(c).upper() == tid
            for c in [
                _pf(r, "identifier"), _pf(r, "callIdentifier"), _pf(r, "reference"),
                _pf(md, "identifier"), _pf(md, "callIdentifier"),
            ]
        ):
            return r
    first_id = str(_pf(results[0], "identifier") or _pf(results[0], "callIdentifier") or "").upper()
    if first_id and (first_id.startswith(tid[:12]) or tid.startswith(first_id[:12])):
        return results[0]
    return None

def parse_topic_json(query_key: str, result_item: dict) -> dict:
    ri, md = result_item, _sg(result_item, "metadata", {})

    title           = _pf(ri, "title")        or _pf(md, "title")
    deadlineDate    = _pf(ri, "deadlineDate") or _pf(md, "deadlineDate")
    startDate       = _pf(ri, "startDate")    or _pf(md, "startDate")
    summary         = _sg(ri, "summary")      or _sg(md, "description") or _sg(md, "shortDescription")
    url             = _pf(ri, "url")          or _pf(md, "url")
    status          = _pf(md, "status")
    typesOfAction   = _pf(md, "typesOfAction")
    programmePeriod = _sg(md, "programmePeriod") or _pf(md, "frameworkProgramme")

    call_description = (
        get_description_byte(ri)
        or _sg(ri, "scope") or _sg(md, "scope")
        or _sg(ri, "expectedOutcomes") or _sg(md, "expectedOutcomes")
        or ""
    )

    call_id          = _sg(ri, "callIdentifier")
    topic_id_resolved = (
        call_id if call_id and str(call_id).startswith("HORIZON") else None
    ) or _sg(ri, "identifier") or query_key

    if isinstance(status, dict):
        status = status.get("id") or status.get("label") or str(status)

    if isinstance(typesOfAction, list):
        typesOfAction = "; ".join(
            (t.get("abbreviation") or t.get("name") or str(t)) if isinstance(t, dict) else str(t)
            for t in typesOfAction
        )

    if not url:
        url = (
            "https://ec.europa.eu/info/funding-tenders/opportunities/portal/"
            f"screen/opportunities/topic-details/{topic_id_resolved}"
        )

    def _flat(v):
        if v is None or (isinstance(v, float) and v != v):
            return ""
        return json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)

    return {
        "topic_id":         _flat(topic_id_resolved),
        "title":            _flat(title),
        "call_description": _flat(call_description),
        "summary":          _flat(summary),
        "status":           _flat(status),
        "deadline":         _flat(deadlineDate),
        "opening_date":     _flat(startDate),
        "type_of_action":   _flat(typesOfAction),
        "programme_period": _flat(programmePeriod),
        "url":              _flat(url),
        "raw_json":         json.dumps(ri, ensure_ascii=False),
    }

# ── DB: Calls ─────────────────────────────────────────────────────────────────

def get_existing_topic_ids() -> set:
    return {r[0] for r in db_query("SELECT topic_id FROM horizon_calls")}

def save_calls(call_rows: list) -> tuple:
    if not call_rows:
        return 0, []
    conn   = get_conn()
    saved, failed = 0, []
    sql    = f"INSERT OR REPLACE INTO horizon_calls VALUES ({_CALLS_PH})"
    for row in call_rows:
        try:
            conn.execute(sql, tuple(row[c] for c in CALLS_COLS))
            saved += 1
        except Exception as e:
            failed.append((row.get("topic_id", "?"), str(e)))
    conn.commit()
    conn.close()
    return saved, failed

def load_calls_df(include_raw_json: bool = False) -> pd.DataFrame:
    if include_raw_json:
        cols, sql = CALLS_COLS, "SELECT * FROM horizon_calls"
    else:
        cols = [c for c in CALLS_COLS if c != "raw_json"]
        sql  = f"SELECT {', '.join(cols)} FROM horizon_calls"
    return pd.DataFrame(db_query(sql), columns=cols)

def load_active_calls_df() -> pd.DataFrame:
    """Load Open/Forthcoming calls that are IA or RIA only."""
    cols          = [c for c in CALLS_COLS if c != "raw_json"]
    active_statuses = tuple(s.lower() for s in
        ("31094501", "31094502", "open", "forthcoming", "Open", "Forthcoming"))
    rows = db_query(
        f"SELECT {', '.join(cols)} FROM horizon_calls "
        f"WHERE LOWER(status) IN ({','.join(['?']*len(active_statuses))})",
        active_statuses,
    )
    df = pd.DataFrame(rows, columns=cols)
    # Filter to IA / RIA only
    if not df.empty:
        df = df[df["type_of_action"].apply(is_allowed_action_type)].reset_index(drop=True)
    return df

# ── Auth ──────────────────────────────────────────────────────────────────────

def do_signup(email: str, name: str, password: str):
    email = email.strip().lower()
    try:
        db_query(
            "INSERT INTO users (email,name,password_hash,created_at) VALUES (?,?,?,?)",
            (email, name.strip(), sha256(password), now_iso()), write=True,
        )
        return db_query("SELECT id,email,name FROM users WHERE email=?", (email,), fetch="one")
    except sqlite3.IntegrityError:
        return None

def do_login(email: str, password: str):
    row = db_query(
        "SELECT id,email,name,password_hash FROM users WHERE email=?",
        (email.strip().lower(),), fetch="one",
    )
    if row and row[3] == sha256(password):
        return row[:3]
    return None

def validate_session():
    if st.session_state.get("_session_validated"):
        return
    user = st.session_state.get("user")
    if user and not db_query("SELECT id FROM users WHERE id=?", (user.get("id"),), fetch="one"):
        st.session_state.user = None
    st.session_state["_session_validated"] = True

def require_login():
    if not st.session_state.get("user"):
        st.warning("Please sign in first.")
        st.stop()

# ── Org Profile ───────────────────────────────────────────────────────────────

def profile_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:16]

def save_org_profile(user_id: int, text: str):
    emb = embed_text(text)
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO org_profile (user_id,profile_text,embedding,updated_at) VALUES (?,?,?,?)",
        (user_id, text, emb.tobytes(), now_iso()),
    )
    conn.commit()
    conn.close()

def load_org_profile(user_id: int):
    row = db_query(
        "SELECT profile_text,embedding FROM org_profile WHERE user_id=?",
        (user_id,), fetch="one",
    )
    if not row:
        return "", None
    return row[0], np.frombuffer(row[1], dtype=np.float32)

# ── Interested Calls ──────────────────────────────────────────────────────────

def add_interested(user_id: int, topic_id: str):
    try:
        db_query(
            "INSERT INTO user_interested_calls (user_id,topic_id,created_at) VALUES (?,?,?)",
            (user_id, topic_id, now_iso()), write=True,
        )
    except sqlite3.IntegrityError:
        pass

def remove_interested(user_id: int, topic_id: str):
    db_query("DELETE FROM user_interested_calls WHERE user_id=? AND topic_id=?",
             (user_id, topic_id), write=True)

def get_interested_calls(user_id: int) -> pd.DataFrame:
    cols = [c for c in CALLS_COLS if c != "raw_json"]
    rows = db_query(
        f"SELECT {', '.join('hc.' + c for c in cols)} "
        "FROM user_interested_calls uic "
        "JOIN horizon_calls hc ON hc.topic_id = uic.topic_id "
        "WHERE uic.user_id = ? ORDER BY uic.created_at DESC",
        (user_id,),
    )
    return pd.DataFrame(rows, columns=cols)

# ── FAISS shortlisting ────────────────────────────────────────────────────────

def _df_hash(df: pd.DataFrame) -> str:
    return hashlib.md5(pd.util.hash_pandas_object(df).values.tobytes()).hexdigest()

def _get_or_build_index(active_df: pd.DataFrame):
    h = _df_hash(active_df)
    cached = st.session_state.get("_faiss_cache")
    if cached and cached["hash"] == h:
        return cached["index"]
    texts = (
        active_df["title"].fillna("") + "\n"
        + active_df["summary"].fillna("") + "\n"
        + active_df["call_description"].fillna("")
    ).tolist()
    emb   = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)
    st.session_state["_faiss_cache"] = {"hash": h, "index": index, "df": active_df}
    return index

def faiss_shortlist(profile_emb: np.ndarray, k: int, active_df: pd.DataFrame, index) -> pd.DataFrame:
    if index is None or active_df.empty:
        return pd.DataFrame()
    D, I = index.search(profile_emb.reshape(1, -1), min(int(k), len(active_df)))
    shortlist = active_df.iloc[I[0]].copy()
    shortlist["semantic_score"] = D[0]
    return shortlist.sort_values("semantic_score", ascending=False)

# ── Analysis cache ────────────────────────────────────────────────────────────

def load_cached_analyses(phash: str, topic_ids: list) -> dict:
    if not topic_ids:
        return {}
    ph = ",".join("?" * len(topic_ids))
    rows = db_query(
        f"SELECT topic_id,score,verdict,strengths,gaps FROM analysis_cache "
        f"WHERE profile_hash=? AND topic_id IN ({ph})",
        (phash, *topic_ids),
    )
    return {
        r[0]: {"score": r[1], "verdict": r[2], "strengths": r[3], "gaps": r[4]}
        for r in (rows or [])
    }

def save_analysis(phash, topic_id, score, verdict, strengths, gaps):
    db_query(
        "INSERT OR REPLACE INTO analysis_cache "
        "(profile_hash,topic_id,score,verdict,strengths,gaps,created_at) VALUES (?,?,?,?,?,?,?)",
        (phash, topic_id, score, verdict, strengths, gaps, now_iso()), write=True,
    )

# ── Call Analyzer Agent ───────────────────────────────────────────────────────

_ANALYZER_SYSTEM = """You are a senior EU funding advisor. Evaluate how well a Horizon Europe call
matches an organization's profile. Respond with a single valid JSON object only — no markdown, no extra text.

Required schema:
{"score":<int 0-100>,"verdict":"<one sentence>","strengths":["<s1>","<s2>"],"gaps":["<g1>"]}

Scoring: 80-100 Strong fit | 60-79 Good fit | 40-59 Partial fit | 20-39 Weak fit | 0-19 Not a fit"""

# Groq free tier: ~14,400 tokens/min on llama-3.3-70b-versatile.
# To stay safely within limits we use a compact model and trimmed prompts.
_GROQ_MODEL        = "llama-3.1-8b-instant"   # smaller → fast & frugal
_MAX_DESC_CHARS    = 800                        # truncate long descriptions
_GROQ_DELAY_SEC    = 4                          # pause between calls (~15 req/min safe margin)

def _trim(text: str, limit: int = _MAX_DESC_CHARS) -> str:
    return text[:limit] + "…" if len(text) > limit else text

def analyze_call(call_row, profile_text: str, api_key: str) -> dict:
    prompt = (
        f"### ORG PROFILE\n{_trim(profile_text, 600)}\n\n"
        f"### CALL\nTitle: {call_row.get('title','')}\n"
        f"Type: {call_row.get('type_of_action','')}\n"
        f"Description:\n{_trim(call_row.get('call_description','') or call_row.get('summary',''))}"
    )
    try:
        resp = Groq(api_key=api_key).chat.completions.create(
            model=_GROQ_MODEL,
            messages=[
                {"role": "system", "content": _ANALYZER_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.0,
            max_tokens=300,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^`(?:json)?\s*|\s*`$", "", raw, flags=re.DOTALL).strip()
        result = json.loads(raw)
        return {
            "score":     int(result.get("score", 0)),
            "verdict":   str(result.get("verdict", "")),
            "strengths": result.get("strengths", []),
            "gaps":      result.get("gaps", []),
        }
    except json.JSONDecodeError as e:
        return {"score": -1, "verdict": f"JSON parse error: {e}", "strengths": [], "gaps": []}
    except Exception as e:
        return {"score": -1, "verdict": f"LLM error: {e}", "strengths": [], "gaps": []}

def run_call_analyzer(shortlist: pd.DataFrame, profile_text: str,
                      phash: str, n_analyze: int, progress_cb=None) -> pd.DataFrame:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        st.error("GROQ_API_KEY not set. The agent cannot run without it.")
        return pd.DataFrame()

    candidates = shortlist.head(int(n_analyze)).copy()
    topic_ids  = candidates["topic_id"].tolist()
    cached     = load_cached_analyses(phash, topic_ids)
    to_fetch   = [tid for tid in topic_ids if tid not in cached]
    total      = len(to_fetch)
    results    = {}

    for i, tid in enumerate(to_fetch):
        if progress_cb:
            progress_cb(i, total, tid)
        row     = candidates[candidates["topic_id"] == tid].iloc[0]
        outcome = analyze_call(row, profile_text, api_key)
        save_analysis(phash, tid, outcome["score"], outcome["verdict"],
                      json.dumps(outcome["strengths"]), json.dumps(outcome["gaps"]))
        results[tid] = outcome
        if i < total - 1:
            time.sleep(_GROQ_DELAY_SEC)   # rate-limit guard

    all_results = {**cached, **results}

    for v in all_results.values():
        for key in ("strengths", "gaps"):
            if isinstance(v[key], str):
                try:
                    v[key] = json.loads(v[key])
                except Exception:
                    v[key] = [v[key]]

    candidates["llm_score"] = candidates["topic_id"].map(lambda t: all_results.get(t, {}).get("score", -1))
    candidates["verdict"]   = candidates["topic_id"].map(lambda t: all_results.get(t, {}).get("verdict", ""))
    candidates["strengths"] = candidates["topic_id"].map(lambda t: all_results.get(t, {}).get("strengths", []))
    candidates["gaps"]      = candidates["topic_id"].map(lambda t: all_results.get(t, {}).get("gaps", []))

    return candidates.sort_values("llm_score", ascending=False).reset_index(drop=True)

# ── Groq contribution idea ────────────────────────────────────────────────────

def groq_contribution_idea(call_row, profile_text: str) -> tuple:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return "", "GROQ_API_KEY not set. Please export it and restart."
    prompt = (
        "You are an expert EU proposal writer.\n\n"
        f"### CALL\nTitle: {call_row.get('title','')}\n"
        f"Description: {_trim(call_row.get('call_description',''), 800)}\n"
        f"Type: {call_row.get('type_of_action','')}\n"
        f"Deadline: {call_row.get('deadline','')}\n\n"
        f"### ORG PROFILE\n{_trim(profile_text, 600)}\n\n"
        "Write a contribution idea with sections:\n"
        "1) Understanding of the Call\n2) Relevance of the Company\n"
        "3) Proposed Technical Contributions\n4) Requirements"
    )
    try:
        resp = Groq(api_key=api_key).chat.completions.create(
            model=_GROQ_MODEL,
            messages=[
                {"role": "system", "content": "You are an expert in EU research proposal writing."},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.25,
            max_tokens=900,
        )
        return prompt, resp.choices[0].message.content
    except Exception as e:
        return prompt, f"LLM error: {e}"

# ── Networking helpers ────────────────────────────────────────────────────────

def suggest_keywords_from_profile(profile_text: str, top_k: int = 12) -> list:
    STOP = {
        "with","that","this","from","have","been","will","their","they",
        "also","such","more","than","which","would","into","about","other","these","some","over",
    }
    tokens = [
        t for t in re.sub(r"[^a-zA-Z0-9\s-]", " ", profile_text.lower()).split()
        if len(t) > 3 and t not in STOP
    ]
    return [w for w, _ in Counter(tokens).most_common(top_k)]

def add_networking_bookmark(user_id, title, link, event_date, notes):
    db_query(
        "INSERT INTO networking_bookmarks (user_id,title,link,event_date,notes,created_at) VALUES (?,?,?,?,?,?)",
        (user_id, title, link, event_date, notes, now_iso()), write=True,
    )

def get_networking_bookmarks(user_id):
    return db_query(
        "SELECT id,title,link,event_date,notes,created_at "
        "FROM networking_bookmarks WHERE user_id=? ORDER BY created_at DESC",
        (user_id,),
    )

# ── Shared UI helpers ─────────────────────────────────────────────────────────

def _add_status_label(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["status_label"] = df["status"].map(resolve_status)
    return df

def hero(title: str, subtitle: str, badge: str = None, icon: str = ""):
    badge_html = f'<div class="badge">{badge}</div><br>' if badge else ""
    st.markdown(
        f'<div class="page-hero">{badge_html}'
        f'<h1>{icon} {title}</h1>'
        f'<p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )

def section(label: str):
    st.markdown(f'<div class="section-label">{label}</div>', unsafe_allow_html=True)

def tip(text: str):
    st.markdown(f'<div class="tip-box">💡 {text}</div>', unsafe_allow_html=True)

def metrics_row(items: list):
    """items = list of (value, label)"""
    cols = st.columns(len(items))
    for col, (val, lbl) in zip(cols, items):
        col.markdown(
            f'<div class="metric-card"><div class="metric-val">{val}</div>'
            f'<div class="metric-lbl">{lbl}</div></div>',
            unsafe_allow_html=True,
        )

def _show_idea(call_row, profile_text: str):
    with st.spinner("✨ Generating contribution idea…"):
        prompt, idea = groq_contribution_idea(call_row, profile_text)
    with st.expander("🔍 Inspect LLM prompt"):
        st.text(prompt)
    st.markdown("### 💡 Contribution Idea")
    st.write(idea)

def _render_call_card(row, show_add_btn: bool = True, user_id: int = None):
    status_label = resolve_status(row.get("status", ""))
    pill_html    = status_pill(status_label)
    st.markdown(
        f'<div class="call-card">'
        f'<div class="call-id">{row["topic_id"]}</div>'
        f'<div class="call-title">{row["title"]}</div>'
        f'<div class="call-meta">📅 {row.get("deadline","—")} &nbsp;|&nbsp; '
        f'🔬 {row.get("type_of_action","—")} &nbsp;&nbsp;{pill_html}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

# ── Startup ───────────────────────────────────────────────────────────────────

init_db()
migrate_db()

# ── Pages ─────────────────────────────────────────────────────────────────────

def page_auth():
    st.markdown('<div class="auth-wrapper">', unsafe_allow_html=True)
    st.markdown(
        '<div class="auth-card">'
        '<div class="auth-logo">🔭</div>'
        '<div class="auth-title">Horizon Finder</div>'
        '<div class="auth-sub">AI-powered Horizon Europe call intelligence</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    tab1, tab2 = st.tabs(["🆕 Create Account", "🔑 Sign In"])

    with tab1:
        with st.form("signup"):
            email = st.text_input("Email address")
            name  = st.text_input("Full name")
            pw    = st.text_input("Password", type="password")
            pw2   = st.text_input("Confirm password", type="password")
            ok    = st.form_submit_button("Create Account", use_container_width=True, type="primary")
        if ok:
            if not email or not pw or pw != pw2:
                st.error("Please provide a valid email and matching passwords.")
            elif do_signup(email, name, pw):
                st.success("✅ Account created! Switch to Sign In.")
            else:
                st.error("Email already registered.")

    with tab2:
        with st.form("login"):
            email = st.text_input("Email address", key="login_email")
            pw    = st.text_input("Password", type="password", key="login_pw")
            ok    = st.form_submit_button("Sign In", use_container_width=True, type="primary")
        if ok:
            user = do_login(email, pw)
            if user:
                st.session_state.user = {"id": user[0], "email": user[1], "name": user[2]}
                st.success(f"Welcome back, {user[2]}!")
                time.sleep(0.5)
                st.rerun()
            else:
                st.error("Invalid email or password.")

    st.markdown("</div>", unsafe_allow_html=True)


def page_upload():
    require_login()
    hero(
        "Upload Work Programmes",
        "Extract Horizon Europe topic IDs from official PDFs and fetch call metadata from the F&T API.",
        badge="Step 1 · Data Ingestion",
        icon="📥",
    )

    tip("Download Work Programme PDFs from the <a href='https://ec.europa.eu/info/funding-tenders/opportunities/portal' target='_blank'>Funding & Tenders Portal</a> and upload them here.")

    uploaded = st.file_uploader(
        "Drop your Work Programme PDFs here",
        type=["pdf"], accept_multiple_files=True,
        label_visibility="collapsed",
    )
    if not uploaded:
        section("HOW IT WORKS")
        c1, c2, c3 = st.columns(3)
        c1.info("**① Upload**\nDrop Horizon Europe Work Programme PDFs from the F&T Portal.")
        c2.info("**② Extract**\nTopic IDs are automatically detected using regex pattern matching.")
        c3.info("**③ Fetch**\nCall metadata is pulled from the official F&T Search API and stored locally.")
        return

    existing_ids  = get_existing_topic_ids()
    all_extracted = {}

    for pdf in uploaded:
        with st.spinner(f"Scanning {pdf.name}…"):
            ids = extract_topic_ids_from_pdf(pdf)
        all_extracted[pdf.name] = ids
        new_count = sum(1 for t in ids if t not in existing_ids)
        st.success(f"**{pdf.name}** — {len(ids)} topic IDs found, {new_count} new")
        if ids:
            with st.expander(f"Preview IDs from {pdf.name}"):
                st.write(ids)

    total_new = sum(1 for ids in all_extracted.values() for t in ids if t not in existing_ids)

    metrics_row([
        (sum(len(v) for v in all_extracted.values()), "Total IDs found"),
        (total_new, "New to database"),
        (len(existing_ids), "Already stored"),
    ])

    if total_new == 0:
        st.info("All extracted topic IDs are already in the database.")
        return

    if not st.button("⬇️ Fetch & Save Calls from API", type="primary"):
        return

    rows_to_save, errors = [], []
    for filename, ids in all_extracted.items():
        new_ids = [t for t in ids if t not in existing_ids]
        if not new_ids:
            continue
        progress = st.progress(0, text=f"Fetching from {filename}…")
        for i, tid in enumerate(new_ids):
            progress.progress((i + 1) / len(new_ids), text=f"{tid}")
            raw = fetch_call_by_topic_id(tid)
            if raw:
                rows_to_save.append(parse_topic_json(tid, raw))
            else:
                errors.append(tid)
            time.sleep(0.15)
        progress.empty()

    if rows_to_save:
        saved, failed = save_calls(rows_to_save)
        st.success(f"✅ Saved **{saved}** calls to the database.")
        for tid, err in failed:
            st.warning(f"Could not save {tid}: {err}")
    if errors:
        with st.expander(f"⚠️ {len(errors)} IDs could not be fetched from the API"):
            st.write(errors[:50])


def page_discover():
    require_login()
    hero(
        "Discover Calls",
        "Browse and search Horizon Europe Innovation Actions (IA) and Research & Innovation Actions (RIA).",
        badge="IA & RIA Only",
        icon="🔍",
    )

    calls_df = load_calls_df()
    if calls_df.empty:
        st.warning("No calls in the database yet. Upload Work Programme PDFs first.")
        return

    # Apply IA/RIA filter for display
    calls_df = calls_df[calls_df["type_of_action"].apply(is_allowed_action_type)].reset_index(drop=True)

    section("FILTERS")
    col1, col2, col3 = st.columns([2, 1, 1])
    q_tid       = col1.text_input("🔎 Search by Topic ID or title", placeholder="e.g. HORIZON-CL4-2024…")
    show_status = col2.selectbox("Status", ["All", "Open", "Forthcoming", "Closed"])
    limit       = col3.number_input("Results limit", 10, 1000, 50, 10)

    df = _add_status_label(calls_df)
    if q_tid.strip():
        mask = (
            df["topic_id"].str.contains(q_tid.strip(), case=False, na=False) |
            df["title"].str.contains(q_tid.strip(), case=False, na=False)
        )
        df = df[mask]
    if show_status != "All":
        df = df[df["status_label"] == show_status]

    if df.empty:
        st.info("No matching calls found. Try adjusting your filters.")
        return

    open_n  = len(df[df["status_label"] == "Open"])
    forth_n = len(df[df["status_label"] == "Forthcoming"])
    closed_n= len(df[df["status_label"] == "Closed"])
    metrics_row([
        (len(df), "Matching calls"),
        (open_n, "Open"),
        (forth_n, "Forthcoming"),
        (closed_n, "Closed"),
    ])

    section("CALL LIST")
    df_show = df.head(int(limit))
    st.dataframe(
        df_show[["topic_id", "title", "deadline", "type_of_action", "status_label", "url"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "url": st.column_config.LinkColumn("Link"),
            "status_label": st.column_config.TextColumn("Status"),
        },
    )

    section("CALL DETAILS & SHORTLIST")
    col_pick, col_btn = st.columns([3, 1])
    pick = col_pick.selectbox("Select a call to inspect", df_show["topic_id"].tolist(), label_visibility="collapsed")
    with col_btn:
        if st.button("⭐ Add to Shortlist", type="primary", use_container_width=True):
            add_interested(st.session_state.user["id"], pick)
            st.success(f"Added **{pick}** to your shortlist.")

    if pick:
        row = df_show[df_show["topic_id"] == pick].iloc[0]
        with st.expander("📄 Full Call Details", expanded=True):
            c1, c2 = st.columns(2)
            c1.markdown(f"**Topic ID:** `{row['topic_id']}`")
            c1.markdown(f"**Deadline:** {row['deadline']}")
            c1.markdown(f"**Type of Action:** {row['type_of_action']}")
            c2.markdown(f"**Status:** {resolve_status(row['status'])}")
            c2.markdown(f"**Programme Period:** {row.get('programme_period','—')}")
            c2.markdown(f"**URL:** [{row['url']}]({row['url']})")
            st.divider()
            st.markdown("**Call Description**")
            st.write(row["call_description"] or "*Description not available for this call.*")


def page_interested():
    require_login()
    hero(
        "My Shortlist",
        "Calls you've saved for further review. Generate AI contribution ideas from here.",
        badge="Saved Calls",
        icon="⭐",
    )

    df = get_interested_calls(st.session_state.user["id"])
    if df.empty:
        st.info("Your shortlist is empty. Add calls from Discover or AI Recommendations.")
        tip("Navigate to **Discover Calls** and click '⭐ Add to Shortlist' on any call you're interested in.")
        return

    df = _add_status_label(df)
    metrics_row([
        (len(df), "Saved calls"),
        (len(df[df["status_label"] == "Open"]), "Open"),
        (len(df[df["status_label"] == "Forthcoming"]), "Forthcoming"),
    ])

    st.dataframe(
        df[["topic_id", "title", "deadline", "type_of_action", "status_label", "url"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "url": st.column_config.LinkColumn("Link"),
            "status_label": st.column_config.TextColumn("Status"),
        },
    )

    section("ACTIONS")
    col1, col2 = st.columns(2)
    with col1:
        to_remove = st.selectbox("Remove a call from shortlist", df["topic_id"].tolist())
        if st.button("🗑️ Remove", use_container_width=True):
            remove_interested(st.session_state.user["id"], to_remove)
            st.success("Removed from shortlist.")
            time.sleep(0.4)
            st.rerun()
    with col2:
        to_gen = st.selectbox("Generate contribution idea for", df["topic_id"].tolist(), key="gen_interest")
        if st.button("💡 Generate Idea", use_container_width=True, type="primary"):
            profile_text, _ = load_org_profile(st.session_state.user["id"])
            if not profile_text:
                st.error("Please set up your Organization Profile first.")
            else:
                _show_idea(df[df["topic_id"] == to_gen].iloc[0], profile_text)


def page_profile():
    require_login()
    hero(
        "Organization Profile",
        "Describe your organization so the AI can find and evaluate the most relevant calls for you.",
        badge="Required for Recommendations",
        icon="🏢",
    )

    existing_text, _ = load_org_profile(st.session_state.user["id"])

    if existing_text:
        st.success("✅ Profile saved. You can update it below.")
        kws = suggest_keywords_from_profile(existing_text)
        section("DETECTED KEYWORDS")
        st.markdown(" &nbsp;".join(f"`{k}`" for k in kws))

    section("PROFILE TEXT")
    tip("Include: markets you serve, core competencies, past R&D projects, technologies, TRL levels, and strategic interests. The more detail, the better the recommendations.")
    text = st.text_area(
        "Organization profile",
        value=existing_text or "",
        height=280,
        label_visibility="collapsed",
        placeholder="Example: We are an SME specializing in AI-driven precision agriculture. Our past projects include EU-funded pilots on crop yield prediction using satellite imagery (TRL 5-6). We are seeking calls related to digital agriculture, food security, and climate-smart farming…",
    )

    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("💾 Save Profile", type="primary", use_container_width=True):
            if not text.strip():
                st.error("Please enter a profile description.")
            else:
                with st.spinner("Embedding profile…"):
                    save_org_profile(st.session_state.user["id"], text.strip())
                st.success("Profile saved and embedded!")
                st.rerun()


def page_recommend():
    require_login()
    hero(
        "AI Recommendations",
        "Two-stage pipeline: semantic FAISS shortlisting followed by LLM-powered fit scoring.",
        badge="IA & RIA Only · Powered by Groq",
        icon="🤖",
    )

    profile_text, profile_emb = load_org_profile(st.session_state.user["id"])
    if not profile_text:
        st.error("⚠️ No organization profile found. Please set up your profile first.")
        if st.button("Go to Profile →"):
            st.session_state["_nav"] = "🏢  My Profile"
            st.rerun()
        return

    active_df = load_active_calls_df()
    if active_df.empty:
        st.warning(
            "No Open/Forthcoming IA or RIA calls found in the database. "
            "Upload a recent Work Programme PDF to load active calls."
        )
        return

    phash = profile_hash(profile_text)

    # ── Stage 1 ──
    st.markdown("""
    <div class="step-row">
        <div class="step-item step-active"><div class="step-circle">1</div><div class="step-label">Semantic Shortlist</div></div>
        <div class="step-item step-inactive"><div class="step-circle">2</div><div class="step-label">AI Scoring</div></div>
        <div class="step-item step-inactive"><div class="step-circle">3</div><div class="step-label">Results</div></div>
    </div>""", unsafe_allow_html=True)

    section("STAGE 1 — SEMANTIC SHORTLISTING")
    st.caption(f"Working with **{len(active_df)} Open/Forthcoming IA/RIA** calls.")

    col1, col2 = st.columns(2)
    pool_size = col1.number_input(
        "Candidate pool (FAISS top-K)",
        min_value=5, max_value=min(200, len(active_df)),
        value=min(40, len(active_df)), step=5,
        help="How many calls FAISS pre-selects by embedding similarity.",
    )
    n_analyze = col2.number_input(
        "Calls to score with AI Agent",
        min_value=1, max_value=min(10, int(pool_size)),
        value=min(5, int(pool_size)), step=1,
        help="How many of the shortlisted calls the LLM will score. Keep ≤10 to stay within Groq free limits.",
    )

    tip(f"Analyzing <b>{n_analyze}</b> calls. Keep this ≤10 to avoid Groq free-tier rate limits. Results are cached — re-running with the same profile is instant.")

    if st.button("🔍 Run Semantic Shortlist", type="primary"):
        with st.spinner("Building semantic index…"):
            index = _get_or_build_index(active_df)
        shortlist = faiss_shortlist(profile_emb, int(pool_size), active_df, index)
        st.session_state.shortlist  = shortlist
        st.session_state.agent_recs = None

    if "shortlist" not in st.session_state or st.session_state.shortlist is None:
        return

    shortlist = st.session_state.shortlist
    if shortlist.empty:
        st.info("No candidates found. Try a larger pool size.")
        return

    shortlist_display = _add_status_label(shortlist)
    st.success(f"✅ **{len(shortlist)}** semantically similar calls retrieved.")
    with st.expander("View shortlisted candidates"):
        st.dataframe(
            shortlist_display[["topic_id","title","deadline","status_label","semantic_score"]]
              .rename(columns={"semantic_score": "similarity"}),
            use_container_width=True, hide_index=True,
        )

    st.divider()

    # ── Stage 2 ──
    section("STAGE 2 — AI FIT SCORING")
    st.caption(
        "The AI agent evaluates each candidate's call description against your organization "
        "profile, returning a score (0-100), verdict, strengths, and gaps."
    )

    cached_count = len(load_cached_analyses(phash, shortlist.head(int(n_analyze))["topic_id"].tolist()))
    fresh_count  = int(n_analyze) - cached_count
    if fresh_count > 0:
        est_sec = fresh_count * (3 + _GROQ_DELAY_SEC)
        st.info(f"**{cached_count}** cached · **{fresh_count}** to fetch (~{est_sec}s)")
    else:
        st.success("All calls already cached — results will be instant.")

    if st.button("🤖 Run AI Analysis", type="primary"):
        pb   = st.progress(0, text="Starting agent…")
        stat = st.empty()

        def _progress(i, total, tid):
            if total == 0:
                return
            pb.progress(i / total, text=f"Scoring {i+1}/{total}: {tid}")
            stat.caption(f"Querying LLM for **{tid}**…")

        with st.spinner("Agent running…"):
            agent_recs = run_call_analyzer(shortlist, profile_text, phash, int(n_analyze), _progress)

        pb.empty(); stat.empty()
        st.session_state.agent_recs = agent_recs if not agent_recs.empty else pd.DataFrame()

    if "agent_recs" not in st.session_state or st.session_state.agent_recs is None:
        return
    if st.session_state.agent_recs.empty:
        st.warning("Agent returned no results. Check GROQ_API_KEY.")
        return

    # ── Results ──
    st.divider()
    section("STAGE 3 — RESULTS")
    agent_recs = _add_status_label(st.session_state.agent_recs)

    display_df = agent_recs[[
        "topic_id","title","deadline","type_of_action","status_label","llm_score","verdict",
    ]].rename(columns={"llm_score": "Score (0-100)"})
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    section("DETAILED ASSESSMENTS")
    for _, row in agent_recs.iterrows():
        score = row["llm_score"]
        badge_html = score_badge(score)
        with st.expander(f"{badge_html}  {row['title'][:90]}", expanded=False):
            st.markdown(badge_html, unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            c1.markdown(f"**Topic ID:** `{row['topic_id']}`")
            c1.markdown(f"**Deadline:** {row['deadline']}")
            c2.markdown(f"**Type:** {row['type_of_action']}")
            c2.markdown(f"**URL:** [{row['url']}]({row['url']})")
            st.markdown(f"**Verdict:** {row['verdict']}")
            col_s, col_g = st.columns(2)
            with col_s:
                st.markdown("**✅ Strengths**")
                for s in (row["strengths"] or []):
                    st.markdown(f"- {s}")
            with col_g:
                st.markdown("**⚠️ Gaps**")
                for g in (row["gaps"] or []):
                    st.markdown(f"- {g}")
            with st.expander("📄 Call Description"):
                st.write(row.get("call_description") or "*Not available*")

    st.divider()
    valid_recs = agent_recs[agent_recs["llm_score"] >= 0]
    if valid_recs.empty:
        return

    col_a, col_b = st.columns(2)
    with col_a:
        section("ADD TO SHORTLIST")
        pick = st.selectbox("Select call", valid_recs["topic_id"].tolist(), key="agent_add", label_visibility="collapsed")
        if st.button("⭐ Add to Shortlist", use_container_width=True):
            add_interested(st.session_state.user["id"], pick)
            st.success("Added to shortlist.")
    with col_b:
        section("GENERATE IDEA")
        pick2 = st.selectbox("Select call", valid_recs["topic_id"].tolist(), key="agent_idea", label_visibility="collapsed")
        if st.button("💡 Generate Contribution Idea", use_container_width=True, type="primary"):
            _show_idea(valid_recs[valid_recs["topic_id"] == pick2].iloc[0], profile_text)

    st.divider()
    if st.button("🗑️ Clear AI cache for this profile"):
        db_query("DELETE FROM analysis_cache WHERE profile_hash=?", (phash,), write=True)
        st.session_state.agent_recs = None
        st.success("Cache cleared.")
        st.rerun()


def page_networking():
    require_login()
    hero(
        "Networking & Brokerage",
        "Discover B2B events, find consortium partners, and track opportunities relevant to your profile.",
        badge="Phase 1 · Curated Links",
        icon="🤝",
    )

    section("OFFICIAL EU BROKERAGE PORTALS")
    cols = st.columns(3)
    for i, (name, link) in enumerate(CURATED_LINKS):
        cols[i % 3].link_button(name, link, use_container_width=True)

    profile_text, _ = load_org_profile(st.session_state.user["id"])
    if profile_text:
        kws = suggest_keywords_from_profile(profile_text, top_k=12)
        section("SUGGESTED KEYWORDS FROM YOUR PROFILE")
        st.markdown(" &nbsp;".join(f"`{k}`" for k in kws))
        st.link_button(
            "🔍 Search EEN with these keywords",
            "https://een.ec.europa.eu/partnering-opportunities?query=" + "+".join(kws[:6]),
        )
    else:
        tip("Complete your Organization Profile to get personalized keyword suggestions for partner search.")

    section("BOOKMARK AN EVENT")
    with st.form("bookmark"):
        c1, c2 = st.columns([2, 1])
        title      = c1.text_input("Event title")
        event_date = c2.date_input("Event date")
        link       = st.text_input("Event URL")
        notes      = st.text_area("Notes", placeholder="Contact person, relevance, follow-up actions…", height=80)
        ok         = st.form_submit_button("💾 Save Bookmark", use_container_width=True)
    if ok and title:
        add_networking_bookmark(st.session_state.user["id"], title, link, str(event_date), notes)
        st.success("Bookmark saved!")

    section("MY BOOKMARKS")
    rows = get_networking_bookmarks(st.session_state.user["id"])
    if rows:
        st.dataframe(
            pd.DataFrame(rows, columns=["id","title","link","event_date","notes","created_at"])
              .drop(columns=["id"]),
            use_container_width=True, hide_index=True,
            column_config={"link": st.column_config.LinkColumn("Link")},
        )
    else:
        st.info("No bookmarks yet. Save events above to track them here.")

# ── Sidebar & routing ─────────────────────────────────────────────────────────

PAGES = {
    "📥  Upload Programmes":  page_upload,
    "🔍  Discover Calls":     page_discover,
    "⭐  My Shortlist":       page_interested,
    "🤖  AI Recommendations": page_recommend,
    "🏢  My Profile":         page_profile,
    "🤝  Networking":         page_networking,
}

def main():
    st.session_state.setdefault("user", None)
    validate_session()

    # ── Sidebar ──
    with st.sidebar:
        st.markdown(
            '<div style="text-align:center;padding:1rem 0 0.5rem;">'
            '<span style="font-size:2.5rem;">🔭</span><br>'
            '<span style="font-size:1.1rem;font-weight:700;color:#e8f4fd;">Horizon Finder</span><br>'
            '<span style="font-size:0.72rem;color:#4a6080;letter-spacing:0.05em;">EU FUNDING INTELLIGENCE</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        st.divider()

        if not st.session_state.get("user"):
            page_auth()
            return

        user = st.session_state.user
        st.markdown(
            f'<div class="user-badge">'
            f'<div class="user-name">👤 {user["name"]}</div>'
            f'<div class="user-email">{user["email"]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        st.markdown('<div class="section-label">NAVIGATE</div>', unsafe_allow_html=True)
        page = st.radio("nav", list(PAGES.keys()), label_visibility="collapsed",
                        key="_nav_radio")

        st.divider()

        # Quick stats
        n_calls      = db_query("SELECT COUNT(*) FROM horizon_calls", fetch="one")
        n_interested = db_query(
            "SELECT COUNT(*) FROM user_interested_calls WHERE user_id=?",
            (user["id"],), fetch="one",
        )
        has_profile = bool(db_query(
            "SELECT 1 FROM org_profile WHERE user_id=?", (user["id"],), fetch="one"
        ))
        st.markdown(
            f'<div style="font-size:0.72rem;color:#4a6080;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px;">Quick Stats</div>'
            f'<div style="font-size:0.82rem;color:#7a90a8;">📦 {n_calls[0] if n_calls else 0} calls stored</div>'
            f'<div style="font-size:0.82rem;color:#7a90a8;">⭐ {n_interested[0] if n_interested else 0} shortlisted</div>'
            f'<div style="font-size:0.82rem;color:{"#48bb78" if has_profile else "#fc8181"};">🏢 Profile {"✓" if has_profile else "not set"}</div>',
            unsafe_allow_html=True,
        )
        st.divider()

        if st.button("🚪 Sign Out", use_container_width=True):
            for k in ("user", "shortlist", "agent_recs", "_session_validated", "_faiss_cache"):
                st.session_state.pop(k, None)
            st.rerun()

    PAGES[page]()

main()