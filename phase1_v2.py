# phase1_v3.py
# Streamlit app for Phase-1 Horizon Europe intelligence platform
# Runs locally with: streamlit run phase1_v3.py

import streamlit as st
import pdfplumber
import re
import os
import json
import time
import hashlib
import sqlite3
import numpy as np
import pandas as pd
import requests
from groq import Groq
from sentence_transformers import SentenceTransformer
import faiss
from datetime import datetime

# ==============================
# App & Constants
# ==============================
st.set_page_config(page_title="Horizon Europe – Phase 1", layout="wide")

DB_PATH = "horizon.db"
API_KEY = "SEDIA"

STATUS_MAP = {
    "31094501": "Open",
    "31094502": "Forthcoming",
    "31094503": "Closed",
    # sometimes the API returns the label directly
    "open": "Open",
    "forthcoming": "Forthcoming",
    "closed": "Closed",
}

FT_SEARCH_BASE = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"

# ==============================
# Model (cached per process)
# ==============================
@st.cache_resource(show_spinner=False)
def load_model():
    return SentenceTransformer("sentence-transformers/all-mpnet-base-v2")


model = load_model()

# ==============================
# Database
# ==============================
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            name TEXT,
            password_hash TEXT,
            created_at TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS org_profile (
            user_id INTEGER UNIQUE,
            profile_text TEXT,
            embedding BLOB,
            updated_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS horizon_calls (
            topic_id TEXT PRIMARY KEY,
            title TEXT,
            scope TEXT,
            expected_outcomes TEXT,
            summary TEXT,
            status TEXT,
            deadline TEXT,
            opening_date TEXT,
            type_of_action TEXT,
            programme_period TEXT,
            url TEXT,
            raw_json TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_interested_calls (
            user_id INTEGER,
            topic_id TEXT,
            created_at TEXT,
            PRIMARY KEY (user_id, topic_id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS networking_bookmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT,
            link TEXT,
            event_date TEXT,
            notes TEXT,
            created_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.commit()
    conn.close()


init_db()

# ==============================
# Utilities
# ==============================
def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso():
    return datetime.utcnow().isoformat()


def embed_text(text: str) -> np.ndarray:
    return model.encode([text], convert_to_numpy=True, normalize_embeddings=True)[0]


# ==============================
# PDF Topic Extraction
# ==============================
# Topic ID regex — mirrors the working notebook exactly.
# Two alternatives cover the full Horizon Europe ID space:
# Alt 1: ...YYYY-<segments>-NN-NN (standard cluster/mission/JU/INFRA calls)
# Alt 2: ...YYYY-<segments>-NN (EIC, ERC single-number suffix calls)
# \w in [\w-]+ covers alphanumeric segments (D3, FARM2FORK, SOIL, etc.)
_RE_TOPIC = re.compile(
    r"\bHORIZON-[A-Z0-9]+-\d{4}-[\w-]+-\d{1,2}-\d{1,2}\b"
    r"|\bHORIZON-[A-Z0-9]+-\d{4}-[\w-]+-\d{2}\b",
    re.IGNORECASE,
)


def _clean_page_text(text: str) -> str:
    """Normalise PDF text: remove soft-hyphens, re-join broken lines, collapse whitespace."""
    text = text.replace("\u00ad", "").replace("\u200b", "")  # soft hyphen / zero-width
    text = re.sub(r"-\n\s*", "-", text)  # re-join line-broken IDs
    text = re.sub(r"\s+", " ", text)
    return text


def extract_topic_ids_from_pdf(uploaded_file) -> list[str]:
    """
    Extract Horizon Europe topic IDs from a PDF using three strategies:
      1. Per-page text (with normalisation) + extract_words() flow
      2. Table cell scan
      3. Raw byte fallback
    Uses the same regex as the working notebook so EIC/ERC single-segment IDs are captured too.
    Post-filter keeps only IDs with >= 4 hyphens.
    Returns a sorted, deduplicated list.
    """
    found: set[str] = set()

    def _scan(text: str):
        for m in _RE_TOPIC.findall(_clean_page_text(text)):
            m_up = m.upper()
            if m_up.startswith("HORIZON-") and m_up.count("-") >= 4:
                found.add(m_up)

    # Strategy 1: per-page pdfplumber extraction
    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            # A) standard text
            _scan(page.extract_text() or "")

            # B) word-flow reassembly (catches IDs split across columns/lines)
            try:
                words = page.extract_words(keep_blank_chars=False, use_text_flow=True)
                _scan(" ".join(w["text"] for w in words))
            except Exception:
                pass

            # C) table cells
            try:
                for table in page.extract_tables():
                    for row in (table or []):
                        for cell in (row or []):
                            if cell:
                                _scan(str(cell))
            except Exception:
                pass

    # Strategy 2: raw byte scan (last resort for non-standard encodings)
    try:
        uploaded_file.seek(0)
        _scan(uploaded_file.read().decode("latin-1", errors="replace"))
        uploaded_file.seek(0)
    except Exception:
        pass

    return sorted(found)


# ==============================
# F&T Portal API
# Mirrors the working notebook approach exactly:
# - quoted text in the URL query param (not in the JSON body)
# - _pick_first() for all list-valued fields
# - deep metadata drilling in parse_topic_json()
# ==============================
def _pick_first(value):
    """Return first element if list/tuple, or the value itself if scalar/None."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _safe_get(dct, key, default=None):
    """Guarded dict.get — returns default if dct is not a dict."""
    if not isinstance(dct, dict):
        return default
    return dct.get(key, default)


def fetch_call_by_topic_id(topic_id: str) -> dict | None:
    """
    Fetch a single call from the F&T Search API.

    Replicates the working notebook approach exactly:

    Strategy A — quoted text in the URL param (most reliable, used by notebook):
      POST ?apiKey=SEDIA&text="<topic_id>"
      body={"pageNumber":1,"pageSize":1}
      Trust the first result — the quoted search is sufficiently precise.

    Strategy B — unquoted text with pageSize=5, pick best validated match.

    Returns the raw result dict or None.
    """
    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    # Strategy A: quoted text in URL — exactly like the working notebook
    # f'...?apiKey={API_KEY}&text="{topic_id}"' — requests encodes the quotes to %22
    url_a = f'{FT_SEARCH_BASE}?apiKey={API_KEY}&text="{topic_id}"'
    body_a = {"pageNumber": 1, "pageSize": 1}
    try:
        r = requests.post(url_a, headers=headers, json=body_a, timeout=20)
        if r.status_code == 200:
            data = r.json()
            results = data.get("results") or []
            if results:
                # Quoted search is precise: trust the first result (same as notebook)
                return results[0]
    except Exception:
        pass

    # Strategy B: unquoted, wider net — validate match before accepting
    url_b = f"{FT_SEARCH_BASE}?apiKey={API_KEY}"
    body_b = {"pageNumber": 1, "pageSize": 5, "text": topic_id}
    try:
        r = requests.post(url_b, headers=headers, json=body_b, timeout=20)
        if r.status_code == 200:
            data = r.json()
            results = data.get("results") or []
            best = _pick_best_result(results, topic_id)
            if best:
                return best
    except Exception:
        pass

    return None


def _pick_best_result(results: list, topic_id: str) -> dict | None:
    """
    From a list of API results, return the one that best matches topic_id.
    Checks: identifier, callIdentifier, reference fields (top-level and in metadata).
    Falls back to returning the first result if nothing matches exactly.
    """
    if not results:
        return None

    tid_upper = topic_id.upper()
    for r in results:
        md = _safe_get(r, "metadata", {})
        candidates = [
            _pick_first(_safe_get(r, "identifier")),
            _pick_first(_safe_get(r, "callIdentifier")),
            _pick_first(_safe_get(r, "reference")),
            _pick_first(_safe_get(md, "identifier")),
            _pick_first(_safe_get(md, "callIdentifier")),
        ]
        for c in candidates:
            if c and str(c).upper() == tid_upper:
                return r

    # Loose match: return first result if its identifier begins with the same prefix
    first_id = str(
        _pick_first(_safe_get(results[0], "identifier"))
        or _pick_first(_safe_get(results[0], "callIdentifier"))
        or ""
    ).upper()
    if first_id and (first_id.startswith(tid_upper[:12]) or tid_upper.startswith(first_id[:12])):
        return results[0]

    return None


def parse_topic_json(query_key: str, result_item: dict) -> dict:
    """
    Parse a single F&T Search API result into our flat DB schema.
    Mirrors the working notebook's parse_topic_json() exactly,
    with the same _pick_first / _safe_get helpers and metadata drilling.

    `query_key` is the topic_id string we searched for.
    """
    # Top-level fields
    identifier = _safe_get(result_item, "identifier")
    callIdentifier = _safe_get(result_item, "callIdentifier")
    reference = _safe_get(result_item, "reference")
    title = _pick_first(_safe_get(result_item, "title"))
    deadlineDate = _pick_first(_safe_get(result_item, "deadlineDate"))
    startDate = _pick_first(_safe_get(result_item, "startDate"))
    summary = _safe_get(result_item, "summary")
    scope = _safe_get(result_item, "scope")
    expectedOut = _safe_get(result_item, "expectedOutcomes")
    url = _pick_first(_safe_get(result_item, "url"))

    # Nested metadata block — the canonical location for many fields
    md = _safe_get(result_item, "metadata", {})
    status = _pick_first(_safe_get(md, "status"))
    typesOfAction = _pick_first(_safe_get(md, "typesOfAction"))
    programmePeriod = _safe_get(md, "programmePeriod")
    frameworkProg = _pick_first(_safe_get(md, "frameworkProgramme"))
    keywords = _safe_get(md, "keywords")

    # Prefer metadata values when top-level fields are absent
    if not title:
        title = _pick_first(_safe_get(md, "title"))
    if not deadlineDate:
        deadlineDate = _pick_first(_safe_get(md, "deadlineDate"))
    if not startDate:
        startDate = _pick_first(_safe_get(md, "startDate"))
    if not summary:
        summary = _safe_get(md, "description") or _safe_get(md, "shortDescription")
    if not scope:
        scope = _safe_get(md, "scope")
    if not expectedOut:
        expectedOut = _safe_get(md, "expectedOutcomes")
    if not url:
        url = _pick_first(_safe_get(md, "url"))

    # Resolve the canonical topic_id to use as DB primary key:
    # prefer callIdentifier (HORIZON-…) > identifier > query_key
    topic_id_resolved = (
        (callIdentifier if callIdentifier and str(callIdentifier).startswith("HORIZON") else None)
        or identifier
        or query_key
    )

    # If status is a dict (some API versions return {id, label}), flatten it
    if isinstance(status, dict):
        status = status.get("id") or status.get("label") or str(status)

    # typesOfAction may be a list of dicts — join abbreviations
    if isinstance(typesOfAction, list):
        typesOfAction = "; ".join(
            (
                (t.get("abbreviation") or t.get("name") or str(t))
                if isinstance(t, dict)
                else str(t)
            )
            for t in typesOfAction
        )

    # Fallback URL
    if not url:
        url = (
            "https://ec.europa.eu/info/funding-tenders/opportunities/portal/"
            f"screen/opportunities/topic-details/{topic_id_resolved}"
        )

    # Flatten any remaining list/dict values to JSON strings for SQLite
    def _flatten(v):
        if v is None:
            return ""
        if isinstance(v, (list, dict)):
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, float) and (v != v):  # NaN check
            return ""
        return str(v)

    return {
        "topic_id": _flatten(topic_id_resolved),
        "title": _flatten(title),
        "scope": _flatten(scope),
        "expected_outcomes": _flatten(expectedOut),
        "summary": _flatten(summary),
        "status": _flatten(status),
        "deadline": _flatten(deadlineDate),
        "opening_date": _flatten(startDate),
        "type_of_action": _flatten(typesOfAction),
        "programme_period": _flatten(programmePeriod or frameworkProg),
        "url": _flatten(url),
        "raw_json": json.dumps(result_item, ensure_ascii=False),
    }


# ==============================
# Database: Calls
# ==============================
def get_existing_topic_ids() -> set:
    conn = get_conn()
    rows = conn.execute("SELECT topic_id FROM horizon_calls").fetchall()
    conn.close()
    return {r[0] for r in rows}


def save_calls(call_rows: list[dict]) -> int:
    if not call_rows:
        return 0
    conn = get_conn()
    saved = 0
    for row in call_rows:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO horizon_calls
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row["topic_id"],
                    row["title"],
                    row["scope"],
                    row["expected_outcomes"],
                    row["summary"],
                    row["status"],
                    row["deadline"],
                    row["opening_date"],
                    row["type_of_action"],
                    row["programme_period"],
                    row["url"],
                    json.dumps(row["raw_json"], ensure_ascii=False),
                ),
            )
            saved += 1
        except Exception as e:
            st.warning(f"Could not save {row.get('topic_id')}: {e}")
    conn.commit()
    conn.close()
    return saved


def load_calls_df() -> pd.DataFrame:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM horizon_calls").fetchall()
    conn.close()
    cols = [
        "topic_id",
        "title",
        "scope",
        "expected_outcomes",
        "summary",
        "status",
        "deadline",
        "opening_date",
        "type_of_action",
        "programme_period",
        "url",
        "raw_json",
    ]
    return pd.DataFrame(rows, columns=cols)


# ==============================
# Auth
# ==============================
def do_signup(email, name, password):
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO users (email, name, password_hash, created_at) VALUES (?,?,?,?)",
            (email.strip().lower(), name.strip(), sha256(password), now_iso()),
        )
        conn.commit()
        user = conn.execute(
            "SELECT id, email, name FROM users WHERE email=?",
            (email.strip().lower(),),
        ).fetchone()
        conn.close()
        return user
    except sqlite3.IntegrityError:
        return None


def do_login(email, password):
    conn = get_conn()
    user = conn.execute(
        "SELECT id, email, name, password_hash FROM users WHERE email=?",
        (email.strip().lower(),),
    ).fetchone()
    conn.close()
    if user and user[3] == sha256(password):
        return (user[0], user[1], user[2])
    return None


def validate_session():
    """
    Called once at the top of main(). If the DB was deleted/reset while the browser
    session was still active, the stored user id will no longer exist in the users table —
    clear the stale session so the login page shows.
    """
    if not st.session_state.get("user"):
        return
    uid = st.session_state.user.get("id")
    if uid is None:
        st.session_state.user = None
        return
    conn = get_conn()
    row = conn.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    if not row:
        st.session_state.user = None


def require_login():
    if not st.session_state.get("user"):
        st.warning("Please sign in first.")
        st.stop()


# ==============================
# Org Profile
# ==============================
def save_org_profile(user_id: int, text: str):
    emb = embed_text(text)
    conn = get_conn()
    exists = conn.execute(
        "SELECT 1 FROM org_profile WHERE user_id=?", (user_id,)
    ).fetchone()
    if exists:
        conn.execute(
            "UPDATE org_profile SET profile_text=?, embedding=?, updated_at=? WHERE user_id=?",
            (text, emb.tobytes(), now_iso(), user_id),
        )
    else:
        conn.execute(
            "INSERT INTO org_profile (user_id, profile_text, embedding, updated_at) VALUES (?,?,?,?)",
            (user_id, text, emb.tobytes(), now_iso()),
        )
    conn.commit()
    conn.close()


def load_org_profile(user_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT profile_text, embedding FROM org_profile WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.close()
    if not row:
        return "", None
    text, blob = row
    emb = np.frombuffer(blob, dtype=np.float32)
    return text, emb


# ==============================
# Interested Calls
# ==============================
def add_interested(user_id: int, topic_id: str):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO user_interested_calls (user_id, topic_id, created_at) VALUES (?,?,?)",
            (user_id, topic_id, now_iso()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()


def remove_interested(user_id: int, topic_id: str):
    conn = get_conn()
    conn.execute(
        "DELETE FROM user_interested_calls WHERE user_id=? AND topic_id=?",
        (user_id, topic_id),
    )
    conn.commit()
    conn.close()


def get_interested_calls(user_id: int) -> pd.DataFrame:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT hc.*
        FROM user_interested_calls uic
        JOIN horizon_calls hc ON hc.topic_id = uic.topic_id
        WHERE uic.user_id=?
        ORDER BY uic.created_at DESC
        """,
        (user_id,),
    ).fetchall()
    conn.close()
    cols = [
        "topic_id",
        "title",
        "scope",
        "expected_outcomes",
        "summary",
        "status",
        "deadline",
        "opening_date",
        "type_of_action",
        "programme_period",
        "url",
        "raw_json",
    ]
    return pd.DataFrame(rows, columns=cols)


# ==============================
# Vector Search (FAISS)
# ==============================
@st.cache_resource(show_spinner=False)
def build_faiss_index(calls_df_hash: str, calls_df: pd.DataFrame):
    """Cache key includes a hash of the dataframe so it rebuilds when data changes."""
    if calls_df.empty:
        return None, None
    texts = (
        calls_df["title"].fillna("")
        + "\n"
        + calls_df["summary"].fillna("")
        + "\n"
        + calls_df["scope"].fillna("")
        + "\n"
        + calls_df["expected_outcomes"].fillna("")
    ).tolist()
    emb = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    d = emb.shape[1]
    index = faiss.IndexFlatIP(d)
    index.add(emb)
    return index, emb


def _df_hash(df: pd.DataFrame) -> str:
    return hashlib.md5(pd.util.hash_pandas_object(df).values.tobytes()).hexdigest()


def recommend_top_n(profile_embedding: np.ndarray, top_n: int, calls_df: pd.DataFrame, index):
    if index is None or calls_df.empty:
        return pd.DataFrame()
    q = profile_embedding.reshape(1, -1)
    D, I = index.search(q, int(top_n))
    recs = calls_df.iloc[I[0]].copy()
    recs["similarity"] = D[0]
    return recs.sort_values("similarity", ascending=False)


# ==============================
# Contribution Idea (Groq LLM)
# ==============================
def groq_contribution_idea(call_row: pd.Series, profile_text: str) -> str:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return "GROQ_API_KEY not set. Please export GROQ_API_KEY in your terminal and restart."
    client = Groq(api_key=api_key)

    prompt = f"""
You are an expert EU proposal writer helping a company craft a concise contribution idea for a Horizon Europe call.

### CALL INFORMATION
Title: {call_row.get("title", "")}
Scope: {call_row.get("scope", "")}
Expected Outcomes: {call_row.get("expected_outcomes", "")}
Type of Action: {call_row.get("type_of_action", "")}
Deadline: {call_row.get("deadline", "")}
URL: {call_row.get("url", "")}

### ORGANIZATION PROFILE
{profile_text}

Write a well-structured contribution idea with these sections:
1) Understanding of the Call — summarise the call's problem and objectives
2) Relevance of the Company — why this organisation is a good fit
3) Proposed Technical Contributions — concrete tasks/work packages the company would lead or contribute to
4) Requirements — what the company needs (partners, resources, TRL level) to deliver
"""
    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.25,
            max_tokens=1100,
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"LLM error: {e}"


# ==============================
# Networking
# ==============================
CURATED_LINKS = [
    ("Funding & Tenders Portal", "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/browse-by-programme"),
    ("Ideal-ist (Digital/ICT brokerage)", "https://www.ideal-ist.eu/"),
    ("Enterprise Europe Network", "https://een.ec.europa.eu/"),
    ("EEN Partnering Opportunities", "https://een.ec.europa.eu/partnering-opportunities"),
    ("Horizon Europe NCP Networks", "https://www.ncpportal.eu/"),
    ("EUREKA Network", "https://www.eurekanetwork.org/"),
]


def suggest_keywords_from_profile(profile_text: str, top_k=12) -> list[str]:
    STOP = {
        "with", "that", "this", "from", "have", "been", "will", "their", "they",
        "also", "such", "more", "than", "which", "would", "into", "about", "other",
        "these", "some", "over"
    }
    txt = re.sub(r"[^a-zA-Z0-9\s\-]", " ", profile_text.lower())
    toks = [t for t in txt.split() if len(t) > 3 and t not in STOP]
    freq: dict[str, int] = {}
    for t in toks:
        freq[t] = freq.get(t, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: x[1], reverse=True)[:top_k]]


def add_networking_bookmark(user_id, title, link, event_date, notes):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO networking_bookmarks (user_id, title, link, event_date, notes, created_at)
        VALUES (?,?,?,?,?,?)
        """,
        (user_id, title, link, event_date, notes, now_iso()),
    )
    conn.commit()
    conn.close()


def get_networking_bookmarks(user_id):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, title, link, event_date, notes, created_at
        FROM networking_bookmarks
        WHERE user_id=?
        ORDER BY created_at DESC
        """,
        (user_id,),
    ).fetchall()
    conn.close()
    return rows


# ==============================
# UI Pages
# ==============================
def page_auth():
    st.header("🔐 Sign up / Login (Demo)")
    tab1, tab2 = st.tabs(["Sign Up", "Login"])

    with tab1:
        with st.form("signup"):
            email = st.text_input("Email")
            name = st.text_input("Name")
            pw = st.text_input("Password", type="password")
            pw2 = st.text_input("Confirm Password", type="password")
            ok = st.form_submit_button("Create account")
            if ok:
                if not email or not pw or pw != pw2:
                    st.error("Please provide a valid email and matching passwords.")
                else:
                    user = do_signup(email, name, pw)
                    if user:
                        st.success("Account created! Please login.")
                    else:
                        st.error("Email already exists.")

    with tab2:
        with st.form("login"):
            email = st.text_input("Email", key="login_email")
            pw = st.text_input("Password", type="password", key="login_pw")
            ok = st.form_submit_button("Login")
            if ok:
                user = do_login(email, pw)
                if user:
                    st.session_state.user = {"id": user[0], "email": user[1], "name": user[2]}
                    st.success(f"Welcome, {user[2]}!")
                    time.sleep(0.6)
                    st.rerun()
                else:
                    st.error("Invalid credentials")


def page_upload():
    require_login()
    st.header("📄 Upload Work Programme PDFs")

    uploaded = st.file_uploader(
        "Upload one or more Horizon Europe Work Programme PDFs",
        type=["pdf"],
        accept_multiple_files=True,
    )
    if not uploaded:
        return

    existing_ids = get_existing_topic_ids()
    all_extracted: dict[str, list[str]] = {}  # filename → topic_ids

    for pdf in uploaded:
        with st.spinner(f"Extracting IDs from {pdf.name} …"):
            ids = extract_topic_ids_from_pdf(pdf)
            all_extracted[pdf.name] = ids
        st.success(f"**{pdf.name}** — found **{len(ids)}** topic IDs")
        if ids:
            with st.expander(f"Preview IDs from {pdf.name}"):
                st.write(ids)

    total_new = sum(
        1 for ids in all_extracted.values() for tid in ids if tid not in existing_ids
    )
    st.info(f"Total new (not yet in DB): **{total_new}** topic IDs across all files.")

    if st.button("🔎 Fetch & Save Calls from API"):
        rows_to_save = []
        errors = []
        for filename, ids in all_extracted.items():
            new_ids = [tid for tid in ids if tid not in existing_ids]
            if not new_ids:
                st.write(f"**{filename}**: all IDs already in DB, skipping.")
                continue

            progress = st.progress(0, text=f"Fetching {filename} …")
            for i, tid in enumerate(new_ids):
                progress.progress((i + 1) / len(new_ids), text=f"Fetching {tid} …")
                raw = fetch_call_by_topic_id(tid)
                if raw:
                    row = parse_topic_json(tid, raw)
                    rows_to_save.append(row)
                else:
                    errors.append(tid)
                time.sleep(0.15)  # polite rate limiting
            progress.empty()

        if rows_to_save:
            count = save_calls(rows_to_save)
            st.success(f"✅ Saved **{count}** calls to database.")

        if errors:
            st.warning(
                "⚠️ Could not fetch **{}** IDs from the API (they may not exist yet or the API returned no match):\n\n{}".format(
                    len(errors),
                    "\n".join(f"- {e}" for e in errors[:30]) + ("…" if len(errors) > 30 else "")
                )
            )


def page_discover():
    require_login()
    st.header("🔎 Discover Calls & Shortlist")
    calls_df = load_calls_df()
    if calls_df.empty:
        st.warning("No calls in database. Please upload PDFs and fetch calls first.")
        return

    col1, col2, col3 = st.columns([1.2, 1, 1])
    with col1:
        q_tid = st.text_input("Search by Topic ID (exact or partial)")
    with col2:
        show_status = st.selectbox("Status", ["All", "Open", "Forthcoming", "Closed"])
    with col3:
        limit = st.number_input("Show first N", 10, 1000, 50, 10)

    df = calls_df.copy()
    df["status_label"] = df["status"].map(
        lambda s: STATUS_MAP.get(str(s), STATUS_MAP.get(str(s).lower(), str(s)))
    )

    if q_tid.strip():
        df = df[df["topic_id"].str.contains(q_tid.strip(), case=False, na=False)]

    if show_status != "All":
        df = df[df["status_label"] == show_status]

    st.caption(f"Showing {min(len(df), int(limit))} of {len(df)} matching calls.")
    if df.empty:
        st.info("No matching calls found.")
        return

    df_show = df.head(int(limit)).copy()
    st.dataframe(
        df_show[["topic_id", "title", "deadline", "type_of_action", "status_label", "url"]],
        use_container_width=True,
    )

    st.subheader("Add to Interested")
    pick = st.selectbox("Select a call", df_show["topic_id"].tolist())
    if st.button("Add to Interested"):
        add_interested(st.session_state.user["id"], pick)
        st.success(f"Added **{pick}** to your Interested list.")

    # Detail view
    if pick:
        row = df_show[df_show["topic_id"] == pick].iloc[0]
        with st.expander("📌 Call Details"):
            st.markdown(f"**Title:** {row['title']}")
            st.markdown(f"**Deadline:** {row['deadline']}")
            st.markdown(f"**Type of Action:** {row['type_of_action']}")
            st.markdown(f"**Status:** {row['status_label']}")
            st.markdown(f"**URL:** [{row['url']}]({row['url']})")
            st.markdown(f"**Scope:**\n\n{row['scope']}")
            st.markdown(f"**Expected Outcomes:**\n\n{row['expected_outcomes']}")


def page_interested():
    require_login()
    st.header("⭐ Interested Calls")
    df = get_interested_calls(st.session_state.user["id"])
    if df.empty:
        st.info("Your Interested list is empty. Add calls from Discover or Recommendations.")
        return

    df["status_label"] = df["status"].map(
        lambda s: STATUS_MAP.get(str(s), STATUS_MAP.get(str(s).lower(), str(s)))
    )
    st.dataframe(
        df[["topic_id", "title", "deadline", "type_of_action", "status_label", "url"]],
        use_container_width=True,
    )

    col1, col2 = st.columns(2)
    with col1:
        to_remove = st.selectbox("Remove a call", df["topic_id"].tolist())
        if st.button("Remove"):
            remove_interested(st.session_state.user["id"], to_remove)
            st.success("Removed.")
            time.sleep(0.4)
            st.rerun()

    with col2:
        to_generate = st.selectbox("Generate idea for", df["topic_id"].tolist(), key="gen_interest")
        if st.button("Generate Contribution Idea"):
            call_row = df[df["topic_id"] == to_generate].iloc[0]
            profile_text, _ = load_org_profile(st.session_state.user["id"])
            if not profile_text:
                st.error("Please create your organization profile first.")
                return
            with st.spinner("Generating …"):
                idea = groq_contribution_idea(call_row, profile_text)
            st.markdown("### Contribution Idea")
            st.write(idea)


def page_profile():
    require_login()
    st.header("🏢 Organization Profile")
    existing_text, _ = load_org_profile(st.session_state.user["id"])
    text = st.text_area(
        "Describe your markets, competencies, prior projects, keywords, and interests.",
        value=existing_text or "",
        height=300,
    )
    if st.button("Save Profile"):
        if not text.strip():
            st.error("Please enter some text.")
        else:
            with st.spinner("Embedding profile …"):
                save_org_profile(st.session_state.user["id"], text.strip())
            st.success("Profile saved and embedded.")


def page_recommend():
    require_login()
    st.header("🧭 Recommendations")

    profile_text, profile_emb = load_org_profile(st.session_state.user["id"])
    if not profile_text:
        st.error("Please create your organization profile first.")
        return

    calls_df = load_calls_df()
    if calls_df.empty:
        st.warning("No calls in database. Please upload PDFs first.")
        return

    h = _df_hash(calls_df)
    index, _ = build_faiss_index(h, calls_df)

    top_n = st.number_input("Top-N", 5, 100, 10, 1)
    if st.button("Find Matching Calls"):
        recs = recommend_top_n(profile_emb, int(top_n), calls_df, index)
        if recs.empty:
            st.info("No recommendations found.")
        else:
            st.session_state.recs = recs

    if "recs" in st.session_state:
        recs = st.session_state.recs
        recs["status_label"] = recs["status"].map(
            lambda s: STATUS_MAP.get(str(s), STATUS_MAP.get(str(s).lower(), str(s)))
        )
        st.dataframe(
            recs[
                [
                    "topic_id",
                    "title",
                    "deadline",
                    "type_of_action",
                    "status_label",
                    "similarity",
                    "url",
                ]
            ],
            use_container_width=True,
        )

        pick = st.selectbox("Add to Interested", recs["topic_id"].tolist())
        if st.button("Add Recommended"):
            add_interested(st.session_state.user["id"], pick)
            st.success("Added to Interested.")

        pick2 = st.selectbox("Generate idea for", recs["topic_id"].tolist(), key="gen_from_recs")
        if st.button("Generate Contribution Idea"):
            call_row = recs[recs["topic_id"] == pick2].iloc[0]
            with st.spinner("Generating …"):
                idea = groq_contribution_idea(call_row, profile_text)
            st.markdown("### Contribution Idea")
            st.write(idea)


def page_networking():
    require_login()
    st.header("🤝 Networking (B2B/Brokerage) — Demo")

    st.subheader("Curated External Links")
    cols = st.columns(3)
    for i, (name, link) in enumerate(CURATED_LINKS):
        with cols[i % 3]:
            st.link_button(name, link)

    profile_text, _ = load_org_profile(st.session_state.user["id"])

    st.subheader("Suggested Keywords")
    if profile_text:
        kws = suggest_keywords_from_profile(profile_text, top_k=12)
        st.write(", ".join(f"**{k}**" for k in kws))
        query = "+".join(kws[:6])
        st.link_button(
            "🔎 Search on EEN with these keywords",
            f"https://een.ec.europa.eu/partnering-opportunities?query={query}",
        )
    else:
        st.write("Create your profile to get suggestions.")

    st.subheader("Bookmark an Event")
    with st.form("bookmark"):
        title = st.text_input("Event Title")
        link = st.text_input("Event Link (URL)")
        event_date = st.date_input("Event Date")
        notes = st.text_area("Notes", "")
        ok = st.form_submit_button("Save Bookmark")
        if ok and title:
            add_networking_bookmark(
                st.session_state.user["id"], title, link, str(event_date), notes
            )
            st.success("Bookmark saved!")

    st.subheader("My Bookmarks")
    rows = get_networking_bookmarks(st.session_state.user["id"])
    if rows:
        dfb = pd.DataFrame(rows, columns=["id", "title", "link", "event_date", "notes", "created_at"])
        st.dataframe(dfb.drop(columns=["id"]), use_container_width=True)
    else:
        st.info("No bookmarks yet.")


# ==============================
# Sidebar & Routing
# ==============================
def sidebar():
    st.sidebar.title("🚀 Horizon Europe")
    if st.session_state.get("user"):
        st.sidebar.write(f"Signed in as: **{st.session_state.user['name']}**")

        # Logout button — handled here directly so it fires before page routing
        if st.sidebar.button("↩️ Logout", use_container_width=True):
            st.session_state.user = None
            st.session_state.pop("recs", None)
            st.rerun()

        page = st.sidebar.radio(
            "Navigate",
            [
                " Upload Work Programmes",
                " Discover & Shortlist",
                " Interested Calls",
                " Recommendations",
                " Organization Profile",
                " Networking",
            ],
        )
        return page

    return " Auth"


def main():
    # Initialise session state
    if "user" not in st.session_state:
        st.session_state.user = None

    # Validate that the stored session still exists in the DB
    # (catches the case where horizon.db was deleted while the browser tab stayed open)
    validate_session()

    page = sidebar()
    if page == " Auth":
        page_auth()
    elif page == " Upload Work Programmes":
        page_upload()
    elif page == " Discover & Shortlist":
        page_discover()
    elif page == " Interested Calls":
        page_interested()
    elif page == " Organization Profile":
        page_profile()
    elif page == " Recommendations":
        page_recommend()
    elif page == " Networking":
        page_networking()


if __name__ == "__main__":
    main()