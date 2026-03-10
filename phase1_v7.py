# phase1_v7.py — Horizon Europe Phase-1 Intelligence Platform
# Run with: streamlit run phase1_v7.py
#
# Architecture:
#   - Agent-based call evaluation (no FAISS / no sentence-transformers)
#   - Screening Agent: batches call summaries and picks top candidates via LLM
#   - Analysis Agent: deep-scores each candidate against org profile
#   - Contribution Agent: generates structured proposal ideas
#   - All results cached in SQLite to respect Groq free-tier limits
#   - RAG-style batching keeps total LLM calls minimal (~3-7 per run)

from __future__ import annotations

import os as _os
import warnings as _warnings

_os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
_warnings.filterwarnings("ignore", message=".*position_ids.*")
_warnings.filterwarnings("ignore", message=".*UNEXPECTED.*")

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Optional

import numpy as np
import pandas as pd
import pdfplumber
import requests
import streamlit as st
from groq import Groq

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Config:
    """Immutable application configuration."""

    DB_PATH: str = "horizon.db"
    API_KEY: str = "SEDIA"
    FT_SEARCH_BASE: str = (
        "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
    )

    # Groq / LLM
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_DELAY_SEC: int = 4
    MAX_DESC_CHARS: int = 800
    MAX_PROFILE_CHARS: int = 600
    LLM_MAX_TOKENS_SCREENING: int = 1200
    LLM_MAX_TOKENS_ANALYSIS: int = 350
    LLM_MAX_TOKENS_IDEA: int = 1000

    # Screening agent: how many calls per LLM batch
    SCREENING_BATCH_SIZE: int = 25

    # Validation
    MAX_PROFILE_LENGTH: int = 15_000
    MIN_PASSWORD_LENGTH: int = 6
    MAX_EMAIL_LENGTH: int = 254

    # UI defaults
    DEFAULT_RESULTS_LIMIT: int = 50
    DEFAULT_ANALYZE_COUNT: int = 5
    API_FETCH_DELAY: float = 0.15


CFG = Config()

ALLOWED_ACTION_TYPES: frozenset[str] = frozenset({
    "IA", "RIA", "HORIZON-IA", "HORIZON-RIA",
    "Innovation Action", "Research and Innovation Action",
    "HORIZON Innovation Actions",
    "HORIZON Research and Innovation Actions",
})

CALLS_COLS: tuple[str, ...] = (
    "topic_id", "title", "call_description", "summary", "status", "deadline",
    "opening_date", "type_of_action", "programme_period", "url", "raw_json",
)
_CALLS_PH: str = ",".join("?" * len(CALLS_COLS))

STATUS_MAP: dict[str, str] = {
    "31094501": "Open", "31094502": "Forthcoming", "31094503": "Closed",
    "open": "Open", "forthcoming": "Forthcoming", "closed": "Closed",
}

CURATED_LINKS: tuple[tuple[str, str], ...] = (
    ("Funding & Tenders Portal",
     "https://ec.europa.eu/info/funding-tenders/opportunities/portal/"
     "screen/opportunities/browse-by-programme"),
    ("Ideal-ist (ICT/Digital)", "https://www.ideal-ist.eu/"),
    ("Enterprise Europe Network", "https://een.ec.europa.eu/"),
    ("EEN Partnering",
     "https://een.ec.europa.eu/partnering-opportunities"),
    ("Horizon NCP Networks", "https://www.ncpportal.eu/"),
    ("EUREKA Network", "https://www.eurekanetwork.org/"),
)

ACTIVE_STATUSES: tuple[str, ...] = (
    "31094501", "31094502", "open", "forthcoming",
)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG & CSS
# ═══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Horizon Finder",
    page_icon="HF",
    layout="wide",
    initial_sidebar_state="expanded",
)

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0f1b35 0%, #1a2d5a 100%);
    border-right: 1px solid rgba(255,255,255,0.08);
}
[data-testid="stSidebar"] * { color: #e8edf5 !important; }
[data-testid="stSidebar"] .stRadio > label {
    color: #a0aec0 !important; font-size: 0.78rem; font-weight: 600;
    letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 4px;
}
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] { gap: 2px; }
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] label {
    background: rgba(255,255,255,0.04); border-radius: 8px; padding: 8px 12px;
    transition: all 0.2s; border: 1px solid transparent;
    color: #c5cfe0 !important; font-size: 0.875rem;
}
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] label:hover {
    background: rgba(99,179,237,0.15); border-color: rgba(99,179,237,0.3);
    color: #90cdf4 !important;
}

.main .block-container { padding-top: 2rem; max-width: 1200px; }

.page-hero {
    background: linear-gradient(135deg, #0f1b35 0%, #1a3a6e 60%, #0d2d5e 100%);
    border-radius: 16px; padding: 2rem 2.5rem; margin-bottom: 2rem;
    border: 1px solid rgba(99,179,237,0.2); position: relative; overflow: hidden;
}
.page-hero::after {
    content: ''; position: absolute; top: -50%; right: -10%;
    width: 350px; height: 350px;
    background: radial-gradient(circle, rgba(99,179,237,0.08) 0%, transparent 70%);
    pointer-events: none;
}
.page-hero h1 { color: #e8f4fd; margin: 0 0 6px; font-size: 1.7rem; font-weight: 700; }
.page-hero p  { color: #90aecb; margin: 0; font-size: 0.95rem; line-height: 1.6; }
.page-hero .badge {
    display: inline-block; background: rgba(99,179,237,0.15); color: #63b3ed;
    border: 1px solid rgba(99,179,237,0.3); border-radius: 20px;
    padding: 2px 12px; font-size: 0.72rem; font-weight: 600;
    letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: 10px;
}

.metric-row { display: flex; gap: 12px; margin-bottom: 1.5rem; flex-wrap: wrap; }
.metric-card {
    flex: 1; min-width: 140px; background: #0f1b35;
    border: 1px solid rgba(99,179,237,0.18); border-radius: 12px; padding: 1.1rem 1.3rem;
}
.metric-card .metric-val { font-size: 1.8rem; font-weight: 700; color: #63b3ed; line-height: 1; }
.metric-card .metric-lbl {
    font-size: 0.75rem; color: #7a90a8; margin-top: 4px;
    text-transform: uppercase; letter-spacing: 0.05em;
}

.call-card {
    background: #0d1a30; border: 1px solid rgba(255,255,255,0.07);
    border-radius: 12px; padding: 1.25rem 1.5rem; margin-bottom: 10px;
    transition: border-color 0.2s;
}
.call-card:hover { border-color: rgba(99,179,237,0.35); }
.call-card .call-id { font-size: 0.72rem; color: #63b3ed; font-family: monospace; font-weight: 600; margin-bottom: 4px; }
.call-card .call-title { font-size: 0.95rem; font-weight: 600; color: #e2eaf6; margin-bottom: 8px; }
.call-card .call-meta { font-size: 0.78rem; color: #7a90a8; }

.pill { display: inline-block; border-radius: 20px; padding: 2px 10px; font-size: 0.72rem; font-weight: 600; }
.pill-open        { background: rgba(72,187,120,0.15); color: #48bb78; border: 1px solid rgba(72,187,120,0.3); }
.pill-forthcoming { background: rgba(237,137,54,0.15); color: #ed8936; border: 1px solid rgba(237,137,54,0.3); }
.pill-closed      { background: rgba(160,174,192,0.15); color: #a0aec0; border: 1px solid rgba(160,174,192,0.3); }

.score-badge { display: inline-block; border-radius: 8px; padding: 4px 12px; font-weight: 700; font-size: 0.9rem; }
.score-high { background: rgba(72,187,120,0.2); color: #68d391; }
.score-mid  { background: rgba(237,137,54,0.2); color: #f6ad55; }
.score-low  { background: rgba(245,101,101,0.2); color: #fc8181; }

.section-label {
    font-size: 0.7rem; font-weight: 700; letter-spacing: 0.1em;
    text-transform: uppercase; color: #4a6080;
    margin: 1.5rem 0 0.5rem; padding-bottom: 6px;
    border-bottom: 1px solid rgba(255,255,255,0.06);
}

.tip-box {
    background: rgba(99,179,237,0.06); border-left: 3px solid #63b3ed;
    border-radius: 0 8px 8px 0; padding: 0.75rem 1rem;
    font-size: 0.85rem; color: #90aecb; margin: 1rem 0;
}

.step-row { display: flex; gap: 0; margin-bottom: 1.5rem; }
.step-item { flex: 1; text-align: center; position: relative; }
.step-circle {
    width: 32px; height: 32px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 6px; font-size: 0.8rem; font-weight: 700;
}
.step-active   .step-circle { background: #63b3ed; color: #0f1b35; }
.step-done     .step-circle { background: #48bb78; color: #0f1b35; }
.step-inactive .step-circle { background: rgba(255,255,255,0.08); color: #4a6080; }
.step-label { font-size: 0.7rem; color: #4a6080; }
.step-active .step-label { color: #63b3ed; }
.step-item::before {
    content: ''; position: absolute; top: 16px; left: -50%; right: 50%;
    height: 2px; background: rgba(255,255,255,0.06); z-index: 0;
}
.step-item:first-child::before { display: none; }

.auth-wrapper { max-width: 420px; margin: 3rem auto; }
.auth-card {
    background: #0d1a30; border: 1px solid rgba(99,179,237,0.18);
    border-radius: 16px; padding: 2rem 2.5rem;
}
.auth-logo  { text-align: center; margin-bottom: 1.5rem; font-size: 1.6rem; font-weight: 700; color: #e8f4fd; }
.auth-title { text-align: center; font-size: 1.3rem; font-weight: 700; color: #e8f4fd; margin-bottom: 0.3rem; }
.auth-sub   { text-align: center; font-size: 0.85rem; color: #7a90a8; margin-bottom: 1.5rem; }

div.stButton > button {
    border-radius: 8px; font-weight: 600; font-size: 0.875rem; transition: all 0.2s;
}
div.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #2b6cb0, #3182ce); border: none; color: white;
}
div.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #3182ce, #4299e1);
    transform: translateY(-1px); box-shadow: 0 4px 12px rgba(49,130,206,0.4);
}

.streamlit-expanderHeader { background: rgba(255,255,255,0.03) !important; border-radius: 8px !important; font-size: 0.88rem !important; }
[data-testid="stDataFrame"] { border-radius: 10px; overflow: hidden; }
[data-testid="stForm"] { background: #0d1a30; border: 1px solid rgba(255,255,255,0.07); border-radius: 12px; padding: 1.5rem; }
.user-badge {
    background: rgba(99,179,237,0.1); border: 1px solid rgba(99,179,237,0.2);
    border-radius: 10px; padding: 0.6rem 0.9rem; margin-bottom: 1rem; font-size: 0.82rem;
}
.user-badge .user-name  { font-weight: 600; color: #90cdf4; }
.user-badge .user-email { color: #718096; font-size: 0.75rem; }
</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(CFG.DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return conn


def db_query(
    sql: str, params: tuple = (), *, fetch: str = "all", write: bool = False,
) -> Any:
    conn = _get_conn()
    try:
        cur = conn.execute(sql, params)
        if write:
            conn.commit()
            return None
        return cur.fetchall() if fetch == "all" else cur.fetchone()
    except Exception:
        if write:
            conn.rollback()
        raise


def db_executemany(sql: str, rows: list[tuple]) -> int:
    conn = _get_conn()
    try:
        conn.executemany(sql, rows)
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise


def init_db() -> None:
    _get_conn().executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
        password_hash TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS org_profile (
        user_id INTEGER UNIQUE NOT NULL, profile_text TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS horizon_calls (
        topic_id TEXT PRIMARY KEY, title TEXT, call_description TEXT,
        summary TEXT, status TEXT, deadline TEXT, opening_date TEXT,
        type_of_action TEXT, programme_period TEXT, url TEXT, raw_json TEXT
    );
    CREATE TABLE IF NOT EXISTS user_interested_calls (
        user_id INTEGER NOT NULL, topic_id TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, topic_id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS networking_bookmarks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL, title TEXT, link TEXT,
        event_date TEXT, notes TEXT, created_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS analysis_cache (
        profile_hash TEXT NOT NULL, topic_id TEXT NOT NULL,
        score INTEGER, verdict TEXT, strengths TEXT, gaps TEXT, created_at TEXT,
        PRIMARY KEY (profile_hash, topic_id)
    );
    """)
    _get_conn().commit()


def migrate_db() -> None:
    conn = _get_conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(horizon_calls)").fetchall()}

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

    # Also drop the embedding column from org_profile if it exists (no longer needed)
    profile_cols = {r[1] for r in conn.execute("PRAGMA table_info(org_profile)").fetchall()}
    # SQLite cannot DROP COLUMN easily; we just ignore the old embedding column if present.

    rows = conn.execute(
        "SELECT topic_id, raw_json FROM horizon_calls "
        "WHERE call_description IS NULL OR call_description = ''"
    ).fetchall()
    if not rows:
        return
    updates: list[tuple[str, str]] = []
    for topic_id, raw_json_str in rows:
        if not raw_json_str:
            continue
        try:
            raw = json.loads(raw_json_str)
        except (json.JSONDecodeError, TypeError):
            continue
        md = raw.get("metadata") or {}
        desc = (
            _get_description_byte(raw)
            or raw.get("scope") or md.get("scope")
            or raw.get("expectedOutcomes") or md.get("expectedOutcomes")
            or ""
        )
        if desc:
            updates.append((desc, topic_id))
    if updates:
        conn.executemany(
            "UPDATE horizon_calls SET call_description=? WHERE topic_id=?", updates
        )
        conn.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _profile_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


def resolve_status(s: Any) -> str:
    s = str(s)
    return STATUS_MAP.get(s, STATUS_MAP.get(s.lower(), s))


_RE_ACTION_SPLIT = re.compile(r"[;,]")


def is_allowed_action_type(toa: str) -> bool:
    if not toa:
        return False
    toa_upper = toa.upper()
    for allowed in ALLOWED_ACTION_TYPES:
        if allowed.upper() in toa_upper:
            return True
    for segment in _RE_ACTION_SPLIT.split(toa):
        if segment.strip().upper() in {"IA", "RIA"}:
            return True
    return False


def _status_pill_html(status_label: str) -> str:
    cls = {"Open": "pill-open", "Forthcoming": "pill-forthcoming"}.get(
        status_label, "pill-closed"
    )
    return f'<span class="pill {cls}">{status_label}</span>'


def _score_badge_html(score: int) -> str:
    if score >= 70:
        cls, lbl = "score-high", "Strong"
    elif score >= 45:
        cls, lbl = "score-mid", "Partial"
    else:
        cls, lbl = "score-low", "Weak"
    return f'<span class="score-badge {cls}">{score}/100 {lbl}</span>'


def _trim(text: str, limit: int = CFG.MAX_DESC_CHARS) -> str:
    return (text[:limit] + "...") if len(text) > limit else text


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self._parts.append(stripped)

    def get_text(self) -> str:
        return "\n".join(self._parts)


def strip_html(html: str) -> str:
    p = _HTMLTextExtractor()
    p.feed(html)
    return p.get_text()


_RE_EMAIL = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def _is_valid_email(email: str) -> bool:
    return bool(_RE_EMAIL.match(email)) and len(email) <= CFG.MAX_EMAIL_LENGTH


# ═══════════════════════════════════════════════════════════════════════════════
# PDF EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

_RE_TOPIC = re.compile(
    r"\bHORIZON-[A-Z0-9]+-\d{4}-[\w-]+-\d{1,2}-\d{1,2}\b"
    r"|\bHORIZON-[A-Z0-9]+-\d{4}-[\w-]+-\d{2}\b",
    re.IGNORECASE,
)
_RE_SOFT_HYPHEN = re.compile(r"[\u00ad\u200b]")
_RE_HYPHEN_NEWLINE = re.compile(r"-\n\s*")
_RE_MULTISPACE = re.compile(r"\s+")


def _scan_text_for_topics(text: str, found: set[str]) -> None:
    text = _RE_SOFT_HYPHEN.sub("", text)
    text = _RE_HYPHEN_NEWLINE.sub("-", text)
    text = _RE_MULTISPACE.sub(" ", text)
    for m in _RE_TOPIC.findall(text):
        m_up = m.upper()
        if m_up.count("-") >= 4:
            found.add(m_up)


def extract_topic_ids_from_pdf(uploaded_file) -> list[str]:
    found: set[str] = set()
    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            _scan_text_for_topics(page.extract_text() or "", found)
            try:
                words_text = " ".join(
                    w["text"] for w in page.extract_words(
                        keep_blank_chars=False, use_text_flow=True
                    )
                )
                _scan_text_for_topics(words_text, found)
            except Exception:
                pass
            try:
                for table in page.extract_tables():
                    for row in table or []:
                        for cell in row or []:
                            if cell:
                                _scan_text_for_topics(str(cell), found)
            except Exception:
                pass
    try:
        uploaded_file.seek(0)
        _scan_text_for_topics(
            uploaded_file.read().decode("latin-1", errors="replace"), found
        )
        uploaded_file.seek(0)
    except Exception:
        pass
    return sorted(found)


# ═══════════════════════════════════════════════════════════════════════════════
# F&T API HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _pick_first(v: Any) -> Any:
    return v[0] if isinstance(v, (list, tuple)) and v else v

def _sg(dct: Any, key: str, default: Any = None) -> Any:
    return dct.get(key, default) if isinstance(dct, dict) else default

def _pf(dct: Any, key: str) -> Any:
    return _pick_first(_sg(dct, key))

def _get_description_byte(result_item: dict) -> str:
    md = _sg(result_item, "metadata") or {}
    raw = _sg(md, "descriptionByte") or ""
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    raw = str(raw).strip()
    return strip_html(raw) if raw else ""


@st.cache_resource(show_spinner=False)
def _get_api_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Accept": "application/json"})
    return s


def fetch_call_by_topic_id(topic_id: str) -> Optional[dict]:
    session = _get_api_session()
    try:
        r = session.post(
            f'{CFG.FT_SEARCH_BASE}?apiKey={CFG.API_KEY}&text="{topic_id}"',
            json={"pageNumber": 1, "pageSize": 1}, timeout=20,
        )
        if r.status_code == 200:
            results = r.json().get("results") or []
            if results:
                return results[0]
    except Exception:
        pass
    try:
        r = session.post(
            f"{CFG.FT_SEARCH_BASE}?apiKey={CFG.API_KEY}",
            json={"pageNumber": 1, "pageSize": 5, "text": topic_id}, timeout=20,
        )
        if r.status_code == 200:
            return _pick_best_result(r.json().get("results") or [], topic_id)
    except Exception:
        pass
    return None


def _pick_best_result(results: list, topic_id: str) -> Optional[dict]:
    if not results:
        return None
    tid = topic_id.upper()
    for r in results:
        md = _sg(r, "metadata", {})
        candidates = [
            _pf(r, "identifier"), _pf(r, "callIdentifier"), _pf(r, "reference"),
            _pf(md, "identifier"), _pf(md, "callIdentifier"),
        ]
        if any(c and str(c).upper() == tid for c in candidates):
            return r
    first_id = str(
        _pf(results[0], "identifier") or _pf(results[0], "callIdentifier") or ""
    ).upper()
    if first_id and (first_id.startswith(tid[:12]) or tid.startswith(first_id[:12])):
        return results[0]
    return None


def parse_topic_json(query_key: str, result_item: dict) -> dict[str, str]:
    ri, md = result_item, _sg(result_item, "metadata", {})
    title = _pf(ri, "title") or _pf(md, "title")
    deadline_date = _pf(ri, "deadlineDate") or _pf(md, "deadlineDate")
    start_date = _pf(ri, "startDate") or _pf(md, "startDate")
    summary = _sg(ri, "summary") or _sg(md, "description") or _sg(md, "shortDescription")
    url = _pf(ri, "url") or _pf(md, "url")
    status = _pf(md, "status")
    types_of_action = _pf(md, "typesOfAction")
    programme_period = _sg(md, "programmePeriod") or _pf(md, "frameworkProgramme")
    call_description = (
        _get_description_byte(ri)
        or _sg(ri, "scope") or _sg(md, "scope")
        or _sg(ri, "expectedOutcomes") or _sg(md, "expectedOutcomes")
        or ""
    )
    call_id = _sg(ri, "callIdentifier")
    topic_id_resolved = (
        (call_id if call_id and str(call_id).startswith("HORIZON") else None)
        or _sg(ri, "identifier") or query_key
    )
    if isinstance(status, dict):
        status = status.get("id") or status.get("label") or str(status)
    if isinstance(types_of_action, list):
        types_of_action = "; ".join(
            (t.get("abbreviation") or t.get("name") or str(t))
            if isinstance(t, dict) else str(t)
            for t in types_of_action
        )
    if not url:
        url = (
            "https://ec.europa.eu/info/funding-tenders/opportunities/portal/"
            f"screen/opportunities/topic-details/{topic_id_resolved}"
        )

    def _flat(v: Any) -> str:
        if v is None or (isinstance(v, float) and v != v):
            return ""
        return json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)

    return {
        "topic_id": _flat(topic_id_resolved), "title": _flat(title),
        "call_description": _flat(call_description), "summary": _flat(summary),
        "status": _flat(status), "deadline": _flat(deadline_date),
        "opening_date": _flat(start_date), "type_of_action": _flat(types_of_action),
        "programme_period": _flat(programme_period), "url": _flat(url),
        "raw_json": json.dumps(ri, ensure_ascii=False),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DB: CALLS
# ═══════════════════════════════════════════════════════════════════════════════

def get_existing_topic_ids() -> set[str]:
    return {r[0] for r in db_query("SELECT topic_id FROM horizon_calls")}


def save_calls(call_rows: list[dict]) -> tuple[int, list[tuple[str, str]]]:
    if not call_rows:
        return 0, []
    sql = f"INSERT OR REPLACE INTO horizon_calls VALUES ({_CALLS_PH})"
    good: list[tuple] = []
    failed: list[tuple[str, str]] = []
    for row in call_rows:
        try:
            good.append(tuple(row[c] for c in CALLS_COLS))
        except KeyError as e:
            failed.append((row.get("topic_id", "?"), f"Missing column: {e}"))
    if good:
        try:
            saved = db_executemany(sql, good)
        except Exception as e:
            return 0, [(r[0], str(e)) for r in good]
    else:
        saved = 0
    return saved, failed


def load_calls_df(include_raw_json: bool = False) -> pd.DataFrame:
    if include_raw_json:
        cols, sql = list(CALLS_COLS), "SELECT * FROM horizon_calls"
    else:
        cols = [c for c in CALLS_COLS if c != "raw_json"]
        sql = f"SELECT {', '.join(cols)} FROM horizon_calls"
    rows = db_query(sql)
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


def load_active_calls_df() -> pd.DataFrame:
    cols = [c for c in CALLS_COLS if c != "raw_json"]
    ph = ",".join(["?"] * len(ACTIVE_STATUSES))
    rows = db_query(
        f"SELECT {', '.join(cols)} FROM horizon_calls WHERE LOWER(status) IN ({ph})",
        tuple(s.lower() for s in ACTIVE_STATUSES),
    )
    df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    if not df.empty:
        df = df[df["type_of_action"].apply(is_allowed_action_type)].reset_index(drop=True)
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════════

def do_signup(email: str, name: str, password: str) -> Optional[tuple]:
    email = email.strip().lower()
    try:
        db_query(
            "INSERT INTO users (email,name,password_hash,created_at) VALUES (?,?,?,?)",
            (email, name.strip(), _sha256(password), _now_iso()), write=True,
        )
        return db_query("SELECT id,email,name FROM users WHERE email=?", (email,), fetch="one")
    except sqlite3.IntegrityError:
        return None


def do_login(email: str, password: str) -> Optional[tuple]:
    row = db_query(
        "SELECT id,email,name,password_hash FROM users WHERE email=?",
        (email.strip().lower(),), fetch="one",
    )
    if row and row[3] == _sha256(password):
        return row[:3]
    return None


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


# ═══════════════════════════════════════════════════════════════════════════════
# ORG PROFILE (no embedding — profile is sent directly to agents)
# ═══════════════════════════════════════════════════════════════════════════════

def save_org_profile(user_id: int, text: str) -> None:
    db_query(
        "INSERT OR REPLACE INTO org_profile (user_id,profile_text,updated_at) VALUES (?,?,?)",
        (user_id, text, _now_iso()), write=True,
    )


def load_org_profile(user_id: int) -> str:
    """Return profile text or empty string."""
    row = db_query("SELECT profile_text FROM org_profile WHERE user_id=?", (user_id,), fetch="one")
    return row[0] if row else ""


# ═══════════════════════════════════════════════════════════════════════════════
# INTERESTED CALLS
# ═══════════════════════════════════════════════════════════════════════════════

def add_interested(user_id: int, topic_id: str) -> None:
    try:
        db_query(
            "INSERT INTO user_interested_calls (user_id,topic_id,created_at) VALUES (?,?,?)",
            (user_id, topic_id, _now_iso()), write=True,
        )
    except sqlite3.IntegrityError:
        pass


def remove_interested(user_id: int, topic_id: str) -> None:
    db_query(
        "DELETE FROM user_interested_calls WHERE user_id=? AND topic_id=?",
        (user_id, topic_id), write=True,
    )


def get_interested_calls(user_id: int) -> pd.DataFrame:
    cols = [c for c in CALLS_COLS if c != "raw_json"]
    rows = db_query(
        f"SELECT {', '.join('hc.' + c for c in cols)} "
        "FROM user_interested_calls uic "
        "JOIN horizon_calls hc ON hc.topic_id = uic.topic_id "
        "WHERE uic.user_id = ? ORDER BY uic.created_at DESC",
        (user_id,),
    )
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS CACHE
# ═══════════════════════════════════════════════════════════════════════════════

def load_cached_analyses(phash: str, topic_ids: list[str]) -> dict[str, dict]:
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


def save_analysis(phash: str, tid: str, score: int, verdict: str, strengths: str, gaps: str) -> None:
    db_query(
        "INSERT OR REPLACE INTO analysis_cache "
        "(profile_hash,topic_id,score,verdict,strengths,gaps,created_at) VALUES (?,?,?,?,?,?,?)",
        (phash, tid, score, verdict, strengths, gaps, _now_iso()), write=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# GROQ CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

_RE_CODE_FENCE = re.compile(r"^`{1,3}(?:json)?\s*|\s*`{1,3}$", re.DOTALL)


def _get_groq_client() -> Optional[Groq]:
    key = os.environ.get("GROQ_API_KEY")
    return Groq(api_key=key) if key else None


def _llm_call(client: Groq, system: str, user: str, max_tokens: int, temperature: float = 0.0) -> str:
    """Single Groq LLM call. Returns raw content string."""
    resp = client.chat.completions.create(
        model=CFG.GROQ_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def _parse_json_response(raw: str) -> Any:
    """Strip code fences and parse JSON."""
    cleaned = _RE_CODE_FENCE.sub("", raw).strip()
    return json.loads(cleaned)


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT 1: SCREENING AGENT — Batch-selects relevant calls via LLM
# ═══════════════════════════════════════════════════════════════════════════════

_SCREENING_SYSTEM = """You are an expert EU funding advisor. Given an organization profile and a numbered list of Horizon Europe call summaries, select the calls most relevant to the organization.

Respond with ONLY a JSON array of the call numbers (integers) that are a good or strong fit. Example: [1, 5, 12, 3]

Be selective — only include calls where there is a clear thematic or capability overlap. If none fit, return an empty array: []"""


def _build_call_catalog_text(calls_df: pd.DataFrame) -> list[tuple[str, list[str]]]:
    """Build batched text catalogs of calls for the screening agent.

    Returns list of (catalog_text, [topic_ids_in_batch]).
    Each batch has at most SCREENING_BATCH_SIZE calls.
    """
    batches: list[tuple[str, list[str]]] = []
    n = len(calls_df)
    batch_size = CFG.SCREENING_BATCH_SIZE

    for start in range(0, n, batch_size):
        chunk = calls_df.iloc[start:start + batch_size]
        lines: list[str] = []
        tids: list[str] = []
        for local_idx, (_, row) in enumerate(chunk.iterrows(), 1):
            title = row.get("title", "")
            summary_text = _trim(
                row.get("call_description", "") or row.get("summary", ""),
                300,
            )
            toa = row.get("type_of_action", "")
            lines.append(f"{local_idx}. [{row['topic_id']}] {title}\n   Type: {toa}\n   Summary: {summary_text}")
            tids.append(row["topic_id"])
        batches.append(("\n\n".join(lines), tids))

    return batches


def run_screening_agent(
    active_df: pd.DataFrame,
    profile_text: str,
    client: Groq,
    top_n: int,
    progress_cb=None,
) -> list[str]:
    """Screen all active calls in batches, return list of selected topic_ids."""
    batches = _build_call_catalog_text(active_df)
    selected_tids: list[str] = []

    for batch_idx, (catalog_text, batch_tids) in enumerate(batches):
        if progress_cb:
            progress_cb(batch_idx, len(batches))

        user_prompt = (
            f"### ORGANIZATION PROFILE\n{_trim(profile_text, CFG.MAX_PROFILE_CHARS)}\n\n"
            f"### CALLS (batch {batch_idx + 1}/{len(batches)}, {len(batch_tids)} calls)\n"
            f"{catalog_text}\n\n"
            f"Select up to {top_n} most relevant call numbers from this batch."
        )
        try:
            raw = _llm_call(client, _SCREENING_SYSTEM, user_prompt, CFG.LLM_MAX_TOKENS_SCREENING)
            picks = _parse_json_response(raw)
            if isinstance(picks, list):
                for idx in picks:
                    if isinstance(idx, int) and 1 <= idx <= len(batch_tids):
                        selected_tids.append(batch_tids[idx - 1])
        except Exception:
            pass  # skip batch on error; other batches still run

        if batch_idx < len(batches) - 1:
            time.sleep(CFG.GROQ_DELAY_SEC)

    return selected_tids


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT 2: ANALYSIS AGENT — Deep-scores individual calls
# ═══════════════════════════════════════════════════════════════════════════════

_ANALYSIS_SYSTEM = """You are a senior EU funding advisor. Evaluate how well a Horizon Europe call matches an organization's profile. Respond with a single valid JSON object only — no markdown, no extra text.

Required schema:
{"score":<int 0-100>,"verdict":"<one sentence>","strengths":["<s1>","<s2>"],"gaps":["<g1>"]}

Scoring: 80-100 Strong fit | 60-79 Good fit | 40-59 Partial fit | 20-39 Weak fit | 0-19 Not a fit"""


def analyze_call(call_row: dict, profile_text: str, client: Groq) -> dict:
    prompt = (
        f"### ORG PROFILE\n{_trim(profile_text, CFG.MAX_PROFILE_CHARS)}\n\n"
        f"### CALL\nTitle: {call_row.get('title', '')}\n"
        f"Type: {call_row.get('type_of_action', '')}\n"
        f"Description:\n{_trim(call_row.get('call_description', '') or call_row.get('summary', ''))}"
    )
    try:
        raw = _llm_call(client, _ANALYSIS_SYSTEM, prompt, CFG.LLM_MAX_TOKENS_ANALYSIS)
        result = _parse_json_response(raw)
        return {
            "score": int(result.get("score", 0)),
            "verdict": str(result.get("verdict", "")),
            "strengths": result.get("strengths", []),
            "gaps": result.get("gaps", []),
        }
    except json.JSONDecodeError as e:
        return {"score": -1, "verdict": f"JSON parse error: {e}", "strengths": [], "gaps": []}
    except Exception as e:
        return {"score": -1, "verdict": f"LLM error: {e}", "strengths": [], "gaps": []}


def run_analysis_agent(
    candidates_df: pd.DataFrame,
    profile_text: str,
    phash: str,
    client: Groq,
    progress_cb=None,
) -> pd.DataFrame:
    """Deep-score each candidate. Uses cache first, then LLM for uncached."""
    candidates = candidates_df.copy()
    topic_ids = candidates["topic_id"].tolist()
    cached = load_cached_analyses(phash, topic_ids)
    to_fetch = [tid for tid in topic_ids if tid not in cached]
    total = len(to_fetch)
    results: dict[str, dict] = {}

    for i, tid in enumerate(to_fetch):
        if progress_cb:
            progress_cb(i, total, tid)
        row = candidates[candidates["topic_id"] == tid].iloc[0]
        outcome = analyze_call(row, profile_text, client)
        save_analysis(
            phash, tid, outcome["score"], outcome["verdict"],
            json.dumps(outcome["strengths"]), json.dumps(outcome["gaps"]),
        )
        results[tid] = outcome
        if i < total - 1:
            time.sleep(CFG.GROQ_DELAY_SEC)

    all_results = {**cached, **results}

    for v in all_results.values():
        for key in ("strengths", "gaps"):
            if isinstance(v[key], str):
                try:
                    v[key] = json.loads(v[key])
                except Exception:
                    v[key] = [v[key]] if v[key] else []

    candidates["llm_score"] = candidates["topic_id"].map(lambda t: all_results.get(t, {}).get("score", -1))
    candidates["verdict"] = candidates["topic_id"].map(lambda t: all_results.get(t, {}).get("verdict", ""))
    candidates["strengths"] = candidates["topic_id"].map(lambda t: all_results.get(t, {}).get("strengths", []))
    candidates["gaps"] = candidates["topic_id"].map(lambda t: all_results.get(t, {}).get("gaps", []))

    return candidates.sort_values("llm_score", ascending=False).reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT 3: CONTRIBUTION IDEA GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

def groq_contribution_idea(call_row: dict, profile_text: str) -> tuple[str, str]:
    client = _get_groq_client()
    if not client:
        return "", "GROQ_API_KEY not set. Please export it and restart."
    prompt = (
        "You are an expert EU proposal writer.\n\n"
        f"### CALL\nTitle: {call_row.get('title', '')}\n"
        f"Description: {_trim(call_row.get('call_description', ''), CFG.MAX_DESC_CHARS)}\n"
        f"Type: {call_row.get('type_of_action', '')}\n"
        f"### ORG PROFILE\n{_trim(profile_text, CFG.MAX_PROFILE_CHARS)}\n\n"
        "Write a contribution idea with sections:\n"
        "1) Understanding of the Call\n2) Relevance of the Company (the reason why the company is a good fit for the call or not)\n"
        "3) Proposed Technical Contributions (focusing on the usability, effectiveness and feasibility of the solution)\n"
        "4) Requirements (partners, data sources, hardware)"
    )
    try:
        content = _llm_call(
            client, "You are an expert in EU research proposal writing.",
            prompt, CFG.LLM_MAX_TOKENS_IDEA, temperature=0.25,
        )
        return prompt, content
    except Exception as e:
        return prompt, f"LLM error: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# NETWORKING HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

_STOP_WORDS: frozenset[str] = frozenset({
    "with", "that", "this", "from", "have", "been", "will", "their", "they",
    "also", "such", "more", "than", "which", "would", "into", "about",
    "other", "these", "some", "over",
})
_RE_ALPHA_ONLY = re.compile(r"[^a-zA-Z0-9\s-]")


def suggest_keywords_from_profile(profile_text: str, top_k: int = 12) -> list[str]:
    tokens = [
        t for t in _RE_ALPHA_ONLY.sub(" ", profile_text.lower()).split()
        if len(t) > 3 and t not in _STOP_WORDS
    ]
    return [w for w, _ in Counter(tokens).most_common(top_k)]


def add_networking_bookmark(user_id: int, title: str, link: str, event_date: str, notes: str) -> None:
    db_query(
        "INSERT INTO networking_bookmarks (user_id,title,link,event_date,notes,created_at) VALUES (?,?,?,?,?,?)",
        (user_id, title, link, event_date, notes, _now_iso()), write=True,
    )


def get_networking_bookmarks(user_id: int) -> list[tuple]:
    return db_query(
        "SELECT id,title,link,event_date,notes,created_at "
        "FROM networking_bookmarks WHERE user_id=? ORDER BY created_at DESC",
        (user_id,),
    ) or []


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED UI COMPONENTS
# ═══════════════════════════════════════════════════════════════════════════════

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
    has_profile = bool(db_query(
        "SELECT 1 FROM org_profile WHERE user_id=?", (user_id,), fetch="one",
    ))
    result = (n_calls, n_interested, has_profile)
    st.session_state[cache_key] = result
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# PAGES
# ═══════════════════════════════════════════════════════════════════════════════

def page_auth() -> None:
    st.markdown('<div class="auth-wrapper">', unsafe_allow_html=True)
    st.markdown(
        '<div class="auth-card">'
        '<div class="auth-logo">Horizon Finder</div>'
        '<div class="auth-sub">AI-powered Horizon Europe call intelligence</div>'
        "</div>",
        unsafe_allow_html=True,
    )
    tab_signup, tab_login = st.tabs(["Create Account", "Sign In"])
    with tab_signup:
        with st.form("signup"):
            email = st.text_input("Email address")
            name = st.text_input("Full name")
            pw = st.text_input("Password", type="password")
            pw2 = st.text_input("Confirm password", type="password")
            ok = st.form_submit_button("Create Account", width="stretch", type="primary")
        if ok:
            if not email or not name.strip():
                st.error("Email and name are required.")
            elif not _is_valid_email(email):
                st.error("Please enter a valid email address.")
            elif len(pw) < CFG.MIN_PASSWORD_LENGTH:
                st.error(f"Password must be at least {CFG.MIN_PASSWORD_LENGTH} characters.")
            elif pw != pw2:
                st.error("Passwords do not match.")
            elif do_signup(email, name, pw):
                st.success("Account created. Switch to Sign In.")
            else:
                st.error("An account with this email already exists.")
    with tab_login:
        with st.form("login"):
            email = st.text_input("Email address", key="login_email")
            pw = st.text_input("Password", type="password", key="login_pw")
            ok = st.form_submit_button("Sign In", width="stretch", type="primary")
        if ok:
            if not email or not pw:
                st.error("Please enter both email and password.")
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


def page_upload() -> None:
    require_login()
    hero("Upload Work Programmes",
         "Extract Horizon Europe topic IDs from official PDFs and fetch call metadata from the F&T API.",
         badge="Step 1 - Data Ingestion")
    tip("Download Work Programme PDFs from the "
        "<a href='https://ec.europa.eu/info/funding-tenders/opportunities/portal' "
        "target='_blank'>Funding & Tenders Portal</a> and upload them here.")
    uploaded = st.file_uploader(
        "Drop your Work Programme PDFs here", type=["pdf"],
        accept_multiple_files=True, label_visibility="collapsed",
    )
    if not uploaded:
        section("HOW IT WORKS")
        c1, c2, c3 = st.columns(3)
        c1.info("**1. Upload** -- Drop Horizon Europe Work Programme PDFs from the F&T Portal.")
        c2.info("**2. Extract** -- Topic IDs are automatically detected using regex pattern matching.")
        c3.info("**3. Fetch** -- Call metadata is pulled from the official F&T Search API and stored locally.")
        return
    existing_ids = get_existing_topic_ids()
    all_extracted: dict[str, list[str]] = {}
    for pdf in uploaded:
        with st.spinner(f"Scanning {pdf.name}..."):
            ids = extract_topic_ids_from_pdf(pdf)
        all_extracted[pdf.name] = ids
        new_count = sum(1 for t in ids if t not in existing_ids)
        st.success(f"**{pdf.name}** -- {len(ids)} topic IDs found, {new_count} new")
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
    if not st.button("Fetch and Save Calls from API", type="primary"):
        return
    rows_to_save: list[dict] = []
    errors: list[str] = []
    for filename, ids in all_extracted.items():
        new_ids = [t for t in ids if t not in existing_ids]
        if not new_ids:
            continue
        progress = st.progress(0, text=f"Fetching from {filename}...")
        for i, tid in enumerate(new_ids):
            progress.progress((i + 1) / len(new_ids), text=f"{tid}")
            try:
                raw = fetch_call_by_topic_id(tid)
                if raw:
                    rows_to_save.append(parse_topic_json(tid, raw))
                else:
                    errors.append(tid)
            except Exception:
                errors.append(tid)
            time.sleep(CFG.API_FETCH_DELAY)
        progress.empty()
    if rows_to_save:
        saved, failed = save_calls(rows_to_save)
        st.success(f"Saved **{saved}** calls to the database.")
        for tid, err in failed:
            st.warning(f"Could not save {tid}: {err}")
    if errors:
        with st.expander(f"{len(errors)} IDs could not be fetched from the API"):
            st.write(errors[:50])


def page_discover() -> None:
    require_login()
    hero("Discover Calls",
         "Browse and search Horizon Europe Innovation Actions (IA) and Research & Innovation Actions (RIA).",
         badge="IA & RIA Only")
    calls_df = load_calls_df()
    if calls_df.empty:
        st.warning("No calls in the database yet. Upload Work Programme PDFs first.")
        return
    calls_df = calls_df[calls_df["type_of_action"].apply(is_allowed_action_type)].reset_index(drop=True)
    if calls_df.empty:
        st.info("No IA/RIA calls found. Upload a programme that contains these action types.")
        return
    section("FILTERS")
    col1, col2, col3 = st.columns([2, 1, 1])
    q_tid = col1.text_input("Search by Topic ID or title", placeholder="e.g. HORIZON-CL4-2024...")
    show_status = col2.selectbox("Status", ["All", "Open", "Forthcoming", "Closed"])
    limit = col3.number_input("Results limit", 10, 1000, CFG.DEFAULT_RESULTS_LIMIT, 10)
    df = _add_status_label(calls_df)
    if q_tid.strip():
        q = q_tid.strip()
        mask = df["topic_id"].str.contains(q, case=False, na=False) | df["title"].str.contains(q, case=False, na=False)
        df = df[mask]
    if show_status != "All":
        df = df[df["status_label"] == show_status]
    if df.empty:
        st.info("No matching calls found. Try adjusting your filters.")
        return
    open_n = int((df["status_label"] == "Open").sum())
    forth_n = int((df["status_label"] == "Forthcoming").sum())
    closed_n = int((df["status_label"] == "Closed").sum())
    metrics_row([(len(df), "Matching"), (open_n, "Open"), (forth_n, "Forthcoming"), (closed_n, "Closed")])
    section("CALL LIST")
    df_show = df.head(int(limit))
    st.dataframe(
        df_show[["topic_id", "title", "deadline", "type_of_action", "status_label", "url"]],
        width="stretch", hide_index=True,
        column_config={"url": st.column_config.LinkColumn("Link"), "status_label": st.column_config.TextColumn("Status")},
    )
    section("CALL DETAILS & SHORTLIST")
    col_pick, col_btn = st.columns([3, 1])
    pick = col_pick.selectbox("Select a call to inspect", df_show["topic_id"].tolist(), label_visibility="collapsed")
    with col_btn:
        if st.button("Add to Shortlist", type="primary", width="stretch"):
            add_interested(st.session_state.user["id"], pick)
            st.success(f"Added **{pick}** to your shortlist.")
    if pick:
        row = df_show[df_show["topic_id"] == pick].iloc[0]
        with st.expander("Full Call Details", expanded=True):
            c1, c2 = st.columns(2)
            c1.markdown(f"**Topic ID:** `{row['topic_id']}`")
            c1.markdown(f"**Deadline:** {row['deadline']}")
            c1.markdown(f"**Type of Action:** {row['type_of_action']}")
            c2.markdown(f"**Status:** {resolve_status(row['status'])}")
            c2.markdown(f"**Programme Period:** {row.get('programme_period', '---')}")
            c2.markdown(f"**URL:** [{row['url']}]({row['url']})")
            st.divider()
            st.markdown("**Call Description**")
            st.write(row["call_description"] or "*Description not available for this call.*")


def page_interested() -> None:
    require_login()
    hero("My Shortlist", "Calls you've saved for further review. Generate AI contribution ideas from here.",
         badge="Saved Calls")
    user_id = st.session_state.user["id"]
    df = get_interested_calls(user_id)
    if df.empty:
        st.info("Your shortlist is empty. Add calls from Discover or AI Recommendations.")
        tip("Navigate to <b>Discover Calls</b> and click 'Add to Shortlist' on any call you're interested in.")
        return
    df = _add_status_label(df)
    metrics_row([
        (len(df), "Saved calls"),
        (int((df["status_label"] == "Open").sum()), "Open"),
        (int((df["status_label"] == "Forthcoming").sum()), "Forthcoming"),
    ])
    st.dataframe(
        df[["topic_id", "title", "deadline", "type_of_action", "status_label", "url"]],
        width="stretch", hide_index=True,
        column_config={"url": st.column_config.LinkColumn("Link"), "status_label": st.column_config.TextColumn("Status")},
    )
    section("ACTIONS")
    col1, col2 = st.columns(2)
    topic_list = df["topic_id"].tolist()
    with col1:
        to_remove = st.selectbox("Remove a call from shortlist", topic_list)
        confirm_key = f"confirm_remove_{to_remove}"
        if st.button("Remove", width="stretch"):
            st.session_state[confirm_key] = True
        if st.session_state.get(confirm_key):
            st.warning(f"Remove **{to_remove}** from your shortlist?")
            c_yes, c_no = st.columns(2)
            if c_yes.button("Yes, remove", key=f"yes_{to_remove}", width="stretch"):
                remove_interested(user_id, to_remove)
                st.session_state.pop(confirm_key, None)
                st.rerun()
            if c_no.button("Cancel", key=f"no_{to_remove}", width="stretch"):
                st.session_state.pop(confirm_key, None)
                st.rerun()
    with col2:
        to_gen = st.selectbox("Generate contribution idea for", topic_list, key="gen_interest")
        if st.button("Generate Idea", width="stretch", type="primary"):
            profile_text = load_org_profile(user_id)
            if not profile_text:
                st.error("Please set up your Organization Profile first.")
            else:
                _show_idea(df[df["topic_id"] == to_gen].iloc[0], profile_text)


def page_profile() -> None:
    require_login()
    hero("Organization Profile",
         "Describe your organization so the AI agents can find and evaluate the most relevant calls for you.",
         badge="Required for Recommendations")
    user_id = st.session_state.user["id"]
    existing_text = load_org_profile(user_id)
    if existing_text:
        st.success("Profile saved. You can update it below.")
        kws = suggest_keywords_from_profile(existing_text)
        section("DETECTED KEYWORDS")
        st.markdown(" &nbsp;".join(f"`{k}`" for k in kws))
    section("PROFILE TEXT")
    tip("Include: markets you serve, core competencies, past R&D projects, "
        "technologies, TRL levels, and strategic interests. The more detail, "
        "the better the recommendations.")
    text = st.text_area(
        "Organization profile", value=existing_text or "", height=280,
        label_visibility="collapsed", max_chars=CFG.MAX_PROFILE_LENGTH,
        placeholder="Example: We are an SME specializing in AI-driven precision agriculture. "
                    "Our past projects include EU-funded pilots on crop yield prediction using "
                    "satellite imagery (TRL 5-6). We are seeking calls related to digital "
                    "agriculture, food security, and climate-smart farming...",
    )
    st.caption(f"{len(text):,} / {CFG.MAX_PROFILE_LENGTH:,} characters")
    col1, _ = st.columns([1, 3])
    with col1:
        if st.button("Save Profile", type="primary", width="stretch"):
            stripped = text.strip()
            if not stripped:
                st.error("Please enter a profile description.")
            elif len(stripped) < 50:
                st.error("Profile should be at least 50 characters for meaningful recommendations.")
            else:
                save_org_profile(user_id, stripped)
                st.success("Profile saved!")
                st.rerun()


def page_recommend() -> None:
    require_login()
    hero("AI Recommendations",
         "Two-stage agent pipeline: Screening Agent filters all calls, then Analysis Agent deep-scores the best matches.",
         badge="IA & RIA Only -- Powered by Groq")
    user_id = st.session_state.user["id"]
    profile_text = load_org_profile(user_id)
    if not profile_text:
        st.error("No organization profile found. Please set up your profile first.")
        if st.button("Go to Profile"):
            st.session_state["_nav_radio"] = "My Profile"
            st.rerun()
        return

    active_df = load_active_calls_df()
    if active_df.empty:
        st.warning("No Open/Forthcoming IA or RIA calls found. Upload a recent Work Programme PDF to load active calls.")
        return

    phash = _profile_hash(profile_text)
    n_active = len(active_df)
    n_batches = math.ceil(n_active / CFG.SCREENING_BATCH_SIZE)

    # Step indicators
    st.markdown(
        '<div class="step-row">'
        '<div class="step-item step-active"><div class="step-circle">1</div><div class="step-label">Screening Agent</div></div>'
        '<div class="step-item step-inactive"><div class="step-circle">2</div><div class="step-label">Analysis Agent</div></div>'
        '<div class="step-item step-inactive"><div class="step-circle">3</div><div class="step-label">Results</div></div>'
        "</div>", unsafe_allow_html=True,
    )

    section("STAGE 1 -- SCREENING AGENT")
    st.caption(f"Scanning **{n_active}** active IA/RIA calls across **{n_batches}** batch(es).")

    col1, col2 = st.columns(2)
    top_n_screen = col1.number_input(
        "Max candidates per batch", min_value=3, max_value=20, value=10, step=1,
        help="How many calls the screening agent can select per batch.",
    )
    n_analyze = col2.number_input(
        "Calls to deep-score", min_value=1, max_value=10, value=CFG.DEFAULT_ANALYZE_COUNT, step=1,
        help="How many of the screened calls the analysis agent will score. Keep <= 10 for Groq free limits.",
    )
    tip(f"The screening agent will process {n_batches} batch(es) of ~{CFG.SCREENING_BATCH_SIZE} calls each. "
        f"Then the analysis agent will deep-score up to <b>{n_analyze}</b> top picks. "
        "Results are cached -- re-running with the same profile is instant.")

    if st.button("Run Screening Agent", type="primary"):
        client = _get_groq_client()
        if not client:
            st.error("GROQ_API_KEY not set. The agent cannot run without it.")
            return
        pb = st.progress(0, text="Screening agent starting...")

        def _screen_progress(batch_idx: int, total: int) -> None:
            if total > 0:
                pb.progress((batch_idx + 1) / total, text=f"Processing batch {batch_idx + 1}/{total}...")

        with st.spinner("Screening agent analyzing calls..."):
            selected_tids = run_screening_agent(active_df, profile_text, client, int(top_n_screen), _screen_progress)
        pb.empty()

        if selected_tids:
            # Deduplicate while preserving order
            seen = set()
            unique_tids = []
            for tid in selected_tids:
                if tid not in seen:
                    seen.add(tid)
                    unique_tids.append(tid)
            screened_df = active_df[active_df["topic_id"].isin(set(unique_tids))].copy()
            st.session_state.screened_df = screened_df
            st.session_state.agent_recs = None
        else:
            st.session_state.screened_df = pd.DataFrame()
            st.session_state.agent_recs = None

    if "screened_df" not in st.session_state or st.session_state.screened_df is None:
        return

    screened_df = st.session_state.screened_df
    if screened_df.empty:
        st.info("The screening agent found no matching calls. Try updating your profile with more specific terms.")
        return

    screened_display = _add_status_label(screened_df)
    st.success(f"Screening agent selected **{len(screened_df)}** candidate call(s).")
    with st.expander("View screened candidates"):
        st.dataframe(
            screened_display[["topic_id", "title", "deadline", "status_label", "type_of_action"]],
            width="stretch", hide_index=True,
        )

    st.divider()

    # Stage 2
    section("STAGE 2 -- ANALYSIS AGENT")
    st.caption("The analysis agent evaluates each candidate against your organization profile, returning a score (0-100), verdict, strengths, and gaps.")

    # Limit to n_analyze
    candidates_for_analysis = screened_df.head(int(n_analyze))
    candidate_ids = candidates_for_analysis["topic_id"].tolist()
    cached_count = len(load_cached_analyses(phash, candidate_ids))
    fresh_count = len(candidate_ids) - cached_count
    if fresh_count > 0:
        est_sec = fresh_count * (3 + CFG.GROQ_DELAY_SEC)
        st.info(f"**{cached_count}** cached, **{fresh_count}** to analyze (~{est_sec}s)")
    else:
        st.success("All calls already cached -- results will be instant.")

    if st.button("Run Analysis Agent", type="primary"):
        client = _get_groq_client()
        if not client:
            st.error("GROQ_API_KEY not set.")
            return
        pb = st.progress(0, text="Analysis agent starting...")
        stat = st.empty()

        def _analysis_progress(i: int, total: int, tid: str) -> None:
            if total > 0:
                pb.progress(i / total, text=f"Scoring {i + 1}/{total}: {tid}")
                stat.caption(f"Querying LLM for **{tid}**...")

        with st.spinner("Analysis agent running..."):
            agent_recs = run_analysis_agent(candidates_for_analysis, profile_text, phash, client, _analysis_progress)
        pb.empty()
        stat.empty()
        st.session_state.agent_recs = agent_recs if not agent_recs.empty else pd.DataFrame()

    if "agent_recs" not in st.session_state or st.session_state.agent_recs is None:
        return
    if st.session_state.agent_recs.empty:
        st.warning("Agent returned no results. Check GROQ_API_KEY.")
        return

    st.divider()
    section("STAGE 3 -- RESULTS")
    agent_recs = _add_status_label(st.session_state.agent_recs)
    display_df = agent_recs[
        ["topic_id", "title", "deadline", "type_of_action", "status_label", "llm_score", "verdict"]
    ].rename(columns={"llm_score": "Score (0-100)"})
    st.dataframe(display_df, width="stretch", hide_index=True)

    section("DETAILED ASSESSMENTS")
    for _, row in agent_recs.iterrows():
        score = row["llm_score"]
        badge_html = _score_badge_html(score)
        with st.expander(f"{row['topic_id']} -- {row['title'][:80]}", expanded=False):
            st.markdown(badge_html, unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            c1.markdown(f"**Topic ID:** `{row['topic_id']}`")
            c1.markdown(f"**Deadline:** {row['deadline']}")
            c2.markdown(f"**Type:** {row['type_of_action']}")
            c2.markdown(f"**URL:** [{row['url']}]({row['url']})")
            st.markdown(f"**Verdict:** {row['verdict']}")
            col_s, col_g = st.columns(2)
            with col_s:
                st.markdown("**Strengths**")
                for s in row["strengths"] or []:
                    st.markdown(f"- {s}")
            with col_g:
                st.markdown("**Gaps**")
                for g in row["gaps"] or []:
                    st.markdown(f"- {g}")
            with st.expander("Call Description"):
                st.write(row.get("call_description") or "*Not available*")

    st.divider()
    valid_recs = agent_recs[agent_recs["llm_score"] >= 0]
    if valid_recs.empty:
        return
    valid_topic_list = valid_recs["topic_id"].tolist()
    col_a, col_b = st.columns(2)
    with col_a:
        section("ADD TO SHORTLIST")
        pick = st.selectbox("Select call", valid_topic_list, key="agent_add", label_visibility="collapsed")
        if st.button("Add to Shortlist", width="stretch"):
            add_interested(st.session_state.user["id"], pick)
            st.success("Added to shortlist.")
    with col_b:
        section("GENERATE IDEA")
        pick2 = st.selectbox("Select call", valid_topic_list, key="agent_idea", label_visibility="collapsed")
        if st.button("Generate Contribution Idea", width="stretch", type="primary"):
            _show_idea(valid_recs[valid_recs["topic_id"] == pick2].iloc[0], profile_text)
    st.divider()
    if st.button("Clear AI cache for this profile"):
        st.session_state["_confirm_clear_cache"] = True
    if st.session_state.get("_confirm_clear_cache"):
        st.warning("This will delete all cached AI analyses for your current profile. Continue?")
        c_yes, c_no = st.columns(2)
        if c_yes.button("Yes, clear cache", width="stretch"):
            db_query("DELETE FROM analysis_cache WHERE profile_hash=?", (phash,), write=True)
            st.session_state.agent_recs = None
            st.session_state.screened_df = None
            st.session_state.pop("_confirm_clear_cache", None)
            st.success("Cache cleared.")
            st.rerun()
        if c_no.button("Cancel", key="cancel_clear", width="stretch"):
            st.session_state.pop("_confirm_clear_cache", None)
            st.rerun()


def page_networking() -> None:
    require_login()
    hero("Networking & Brokerage",
         "Discover B2B events, find consortium partners, and track opportunities relevant to your profile.",
         badge="Phase 1 - Curated Links")
    user_id = st.session_state.user["id"]

    section("OFFICIAL EU BROKERAGE PORTALS")
    cols = st.columns(3)
    for i, (name, link) in enumerate(CURATED_LINKS):
        cols[i % 3].link_button(name, link, width="stretch")

    profile_text = load_org_profile(user_id)
    if profile_text:
        kws = suggest_keywords_from_profile(profile_text, top_k=12)
        section("SUGGESTED KEYWORDS FROM YOUR PROFILE")
        st.markdown(" &nbsp;".join(f"`{k}`" for k in kws))
        st.link_button(
            "Search EEN with these keywords",
            "https://een.ec.europa.eu/partnering-opportunities?query=" + "+".join(kws[:6]),
        )
    else:
        tip("Complete your Organization Profile to get personalized keyword suggestions for partner search.")

    section("BOOKMARK AN EVENT")
    with st.form("bookmark"):
        c1, c2 = st.columns([2, 1])
        title = c1.text_input("Event title")
        event_date = c2.date_input("Event date")
        link = st.text_input("Event URL")
        notes = st.text_area("Notes", placeholder="Contact person, relevance, follow-up actions...", height=80)
        ok = st.form_submit_button("Save Bookmark", width="stretch")
    if ok:
        if not title.strip():
            st.error("Event title is required.")
        else:
            add_networking_bookmark(user_id, title.strip(), link, str(event_date), notes)
            st.success("Bookmark saved!")

    section("MY BOOKMARKS")
    rows = get_networking_bookmarks(user_id)
    if rows:
        bm_df = pd.DataFrame(rows, columns=["id", "title", "link", "event_date", "notes", "created_at"])
        st.dataframe(
            bm_df.drop(columns=["id"]), width="stretch", hide_index=True,
            column_config={"link": st.column_config.LinkColumn("Link")},
        )
    else:
        st.info("No bookmarks yet. Save events above to track them here.")


# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR & ROUTING  (Profile is higher; no emojis in nav labels)
# ═══════════════════════════════════════════════════════════════════════════════

PAGES: dict[str, callable] = {
    "My Profile":          page_profile,
    "Upload Programmes":   page_upload,
    "Discover Calls":      page_discover,
    "My Shortlist":        page_interested,
    "AI Recommendations":  page_recommend,
    "Networking":          page_networking,
}


def main() -> None:
    st.session_state.setdefault("user", None)
    validate_session()

    with st.sidebar:
        st.markdown(
            '<div style="text-align:center;padding:1rem 0 0.5rem;">'
            '<span style="font-size:1.1rem;font-weight:700;color:#e8f4fd;">Horizon Finder</span><br>'
            '<span style="font-size:0.72rem;color:#4a6080;letter-spacing:0.05em;">'
            "EU FUNDING INTELLIGENCE</span></div>",
            unsafe_allow_html=True,
        )
        st.divider()

        if not st.session_state.get("user"):
            page_auth()
            return

        user = st.session_state.user
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

    PAGES[page]()


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

init_db()
migrate_db()
main()