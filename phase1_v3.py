# phase1_v3.py — Horizon Europe Phase-1 intelligence platform
# Run with: streamlit run phase1_v3.py

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

# ── Constants ─────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Horizon Europe – Phase 1", layout="wide")
DB_PATH = "horizon.db"
API_KEY = "SEDIA"
FT_SEARCH_BASE = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"

CALLS_COLS = [
    "topic_id", "title", "call_description", "summary", "status", "deadline",
    "opening_date", "type_of_action", "programme_period", "url", "raw_json",
]
_CALLS_PLACEHOLDERS = ",".join("?" * len(CALLS_COLS))

STATUS_MAP = {
    "31094501": "Open",
    "31094502": "Forthcoming",
    "31094503": "Closed",
    "open": "Open",
    "forthcoming": "Forthcoming",
    "closed": "Closed",
}

CURATED_LINKS = [
    ("Funding & Tenders Portal", "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/browse-by-programme"),
    ("Ideal-ist (Digital/ICT)", "https://www.ideal-ist.eu/"),
    ("Enterprise Europe Network", "https://een.ec.europa.eu/"),
    ("EEN Partnering Opportunities", "https://een.ec.europa.eu/partnering-opportunities"),
    ("Horizon Europe NCP Networks", "https://www.ncpportal.eu/"),
    ("EUREKA Network", "https://www.eurekanetwork.org/"),
]

# ── Model ─────────────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def load_model():
    return SentenceTransformer("sentence-transformers/all-mpnet-base-v2")

model = load_model()

# ── Database ──────────────────────────────────────────────────────────────────
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def db_query(sql, params=(), *, fetch="all", write=False):
    """Execute one SQL statement. Returns rows for reads, None for writes."""
    conn = get_conn()
    cur = conn.execute(sql, params)
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
        email TEXT UNIQUE,
        name TEXT,
        password_hash TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS org_profile (
        user_id INTEGER UNIQUE,
        profile_text TEXT,
        embedding BLOB,
        updated_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS horizon_calls (
        topic_id TEXT PRIMARY KEY,
        title TEXT,
        call_description TEXT,
        summary TEXT,
        status TEXT,
        deadline TEXT,
        opening_date TEXT,
        type_of_action TEXT,
        programme_period TEXT,
        url TEXT,
        raw_json TEXT
    );

    CREATE TABLE IF NOT EXISTS user_interested_calls (
        user_id INTEGER,
        topic_id TEXT,
        created_at TEXT,
        PRIMARY KEY (user_id, topic_id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS networking_bookmarks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        link TEXT,
        event_date TEXT,
        notes TEXT,
        created_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)
    conn.commit()
    conn.close()

def migrate_db():
    """
    One-time schema migration (scope+expected_outcomes -> call_description)
    and backfill of call_description from raw_json for any empty rows.
    Uses a single connection throughout to avoid redundant open/close.
    """
    conn = get_conn()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(horizon_calls)").fetchall()}

    # Old schema migration
    if "scope" in cols and "call_description" not in cols:
        conn.execute("ALTER TABLE horizon_calls ADD COLUMN call_description TEXT DEFAULT ''")
        conn.execute("""
            UPDATE horizon_calls
            SET call_description =
                COALESCE(NULLIF(scope, ''), '')
                ||
                CASE WHEN scope != '' AND expected_outcomes != '' THEN char(10) || char(10) ELSE '' END
                ||
                COALESCE(NULLIF(expected_outcomes, ''), '')
        """)
        conn.commit()

    # Backfill missing descriptions
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
        md = raw.get("metadata") or {}
        desc = (
            get_description_byte(raw)
            or raw.get("scope")
            or md.get("scope")
            or raw.get("expectedOutcomes")
            or md.get("expectedOutcomes")
            or ""
        )
        if desc:
            conn.execute(
                "UPDATE horizon_calls SET call_description = ? WHERE topic_id = ?",
                (desc, topic_id),
            )
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
                _scan(" ".join(w["text"] for w in page.extract_words(
                    keep_blank_chars=False, use_text_flow=True)))
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
    """Return first element of a list/tuple, or the value itself."""
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    return v

def _sg(dct, key, default=None):
    """Safe dict.get — returns default if dct is not a dict."""
    return dct.get(key, default) if isinstance(dct, dict) else default

def _pf(dct, key):
    """Safe-get + pick-first."""
    return _pick_first(_sg(dct, key))

def _strip_html(html: str) -> str:
    """Strip HTML tags and decode entities to plain text."""
    from html.parser import HTMLParser

    class _P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []

        def handle_data(self, d):
            if d.strip():
                self.parts.append(d.strip())

        def handle_entityref(self, name):
            pass

    p = _P()
    p.feed(html)
    return "\n".join(p.parts).strip()

def get_description_byte(result_item: dict) -> str:
    md = _sg(result_item, "metadata") or {}
    raw = _sg(md, "descriptionByte") or ""
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    raw = str(raw).strip()
    if not raw:
        return ""
    return _strip_html(raw)

_api_session = requests.Session()
_api_session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

def fetch_call_by_topic_id(topic_id: str):
    """Fetch one call from F&T API using 2 strategies."""
    # Strategy A: quoted match
    try:
        r = _api_session.post(
            f'{FT_SEARCH_BASE}?apiKey={API_KEY}&text="{topic_id}"',
            json={"pageNumber": 1, "pageSize": 1},
            timeout=20
        )
        if r.status_code == 200:
            results = r.json().get("results") or []
            if results:
                return results[0]
    except Exception:
        pass

    # Strategy B: fallback
    try:
        r = _api_session.post(
            f"{FT_SEARCH_BASE}?apiKey={API_KEY}",
            json={"pageNumber": 1, "pageSize": 5, "text": topic_id},
            timeout=20
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
                _pf(r, "identifier"),
                _pf(r, "callIdentifier"),
                _pf(r, "reference"),
                _pf(md, "identifier"),
                _pf(md, "callIdentifier"),
            ]
        ):
            return r
    first_id = str(
        _pf(results[0], "identifier") or _pf(results[0], "callIdentifier") or ""
    ).upper()
    if first_id and (first_id.startswith(tid[:12]) or tid.startswith(first_id[:12])):
        return results[0]
    return None

def parse_topic_json(query_key: str, result_item: dict) -> dict:
    ri = result_item
    md = _sg(result_item, "metadata", {})

    title = _pf(ri, "title") or _pf(md, "title")
    deadlineDate = _pf(ri, "deadlineDate") or _pf(md, "deadlineDate")
    startDate = _pf(ri, "startDate") or _pf(md, "startDate")
    summary = _sg(ri, "summary") or _sg(md, "description") or _sg(md, "shortDescription")
    url = _pf(ri, "url") or _pf(md, "url")
    status = _pf(md, "status")
    typesOfAction = _pf(md, "typesOfAction")
    programmePeriod = _sg(md, "programmePeriod") or _pf(md, "frameworkProgramme")

    call_description = (
        get_description_byte(ri)
        or _sg(ri, "scope")
        or _sg(md, "scope")
        or _sg(ri, "expectedOutcomes")
        or _sg(md, "expectedOutcomes")
        or ""
    )

    call_id = _sg(ri, "callIdentifier")
    topic_id_resolved = (
        call_id if call_id and str(call_id).startswith("HORIZON") else None
    ) or _sg(ri, "identifier") or query_key

    if isinstance(status, dict):
        status = status.get("id") or status.get("label") or str(status)

    if isinstance(typesOfAction, list):
        typesOfAction = "; ".join(
            (t.get("abbreviation") or t.get("name") or str(t))
            if isinstance(t, dict) else str(t)
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
        "topic_id": _flat(topic_id_resolved),
        "title": _flat(title),
        "call_description": _flat(call_description),
        "summary": _flat(summary),
        "status": _flat(status),
        "deadline": _flat(deadlineDate),
        "opening_date": _flat(startDate),
        "type_of_action": _flat(typesOfAction),
        "programme_period": _flat(programmePeriod),
        "url": _flat(url),
        "raw_json": json.dumps(ri, ensure_ascii=False),
    }

# ── DB: Calls ─────────────────────────────────────────────────────────────────
def get_existing_topic_ids() -> set:
    return {r[0] for r in db_query("SELECT topic_id FROM horizon_calls")}

def save_calls(call_rows: list) -> tuple:
    """Bulk-insert call rows. Returns (saved_count, failed_list)."""
    if not call_rows:
        return 0, []
    conn = get_conn()
    saved = 0
    failed = []
    sql = f"INSERT OR REPLACE INTO horizon_calls VALUES ({_CALLS_PLACEHOLDERS})"
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
        sql = f"SELECT {', '.join(cols)} FROM horizon_calls"
    return pd.DataFrame(db_query(sql), columns=cols)

# ── Auth ──────────────────────────────────────────────────────────────────────
def do_signup(email: str, name: str, password: str):
    email = email.strip().lower()
    try:
        db_query(
            "INSERT INTO users (email,name,password_hash,created_at) VALUES (?,?,?,?)",
            (email, name.strip(), sha256(password), now_iso()),
            write=True,
        )
        return db_query(
            "SELECT id,email,name FROM users WHERE email=?",
            (email,), fetch="one"
        )
    except sqlite3.IntegrityError:
        return None

def do_login(email: str, password: str):
    row = db_query(
        "SELECT id,email,name,password_hash FROM users WHERE email=?",
        (email.strip().lower(),), fetch="one"
    )
    if row and row[3] == sha256(password):
        return row[:3]
    return None

def validate_session():
    """Clear session if stored user no longer exists."""
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
def save_org_profile(user_id: int, text: str):
    emb = embed_text(text)
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO org_profile (user_id,profile_text,embedding,updated_at) "
        "VALUES (?,?,?,?)",
        (user_id, text, emb.tobytes(), now_iso()),
    )
    conn.commit()
    conn.close()

def load_org_profile(user_id: int):
    row = db_query(
        "SELECT profile_text,embedding FROM org_profile WHERE user_id=?",
        (user_id,), fetch="one"
    )
    if not row:
        return "", None
    return row[0], np.frombuffer(row[1], dtype=np.float32)

# ── Interested Calls ──────────────────────────────────────────────────────────
def add_interested(user_id: int, topic_id: str):
    try:
        db_query(
            "INSERT INTO user_interested_calls (user_id,topic_id,created_at) VALUES (?,?,?)",
            (user_id, topic_id, now_iso()), write=True
        )
    except sqlite3.IntegrityError:
        pass

def remove_interested(user_id: int, topic_id: str):
    db_query(
        "DELETE FROM user_interested_calls WHERE user_id=? AND topic_id=?",
        (user_id, topic_id), write=True
    )

def get_interested_calls(user_id: int) -> pd.DataFrame:
    cols = [c for c in CALLS_COLS if c != "raw_json"]
    rows = db_query(
        f"SELECT {', '.join('hc.' + c for c in cols)} "
        "FROM user_interested_calls uic "
        "JOIN horizon_calls hc ON hc.topic_id = uic.topic_id "
        "WHERE uic.user_id = ? ORDER BY uic.created_at DESC",
        (user_id,)
    )
    return pd.DataFrame(rows, columns=cols)

# ── FAISS ─────────────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def build_faiss_index(calls_df_hash: str, calls_df: pd.DataFrame):
    """Build and cache a FAISS inner-product index."""
    if calls_df.empty:
        return None
    texts = (
        calls_df["title"].fillna("")
        + "\n"
        + calls_df["summary"].fillna("")
        + "\n"
        + calls_df["call_description"].fillna("")
    ).tolist()
    emb = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)
    return index

def _df_hash(df: pd.DataFrame) -> str:
    return hashlib.md5(pd.util.hash_pandas_object(df).values.tobytes()).hexdigest()

def recommend_top_n(profile_emb: np.ndarray, top_n: int, calls_df: pd.DataFrame, index) -> pd.DataFrame:
    if index is None or calls_df.empty:
        return pd.DataFrame()
    D, I = index.search(profile_emb.reshape(1, -1), int(top_n))
    recs = calls_df.iloc[I[0]].copy()
    recs["similarity"] = D[0]
    return recs.sort_values("similarity", ascending=False)

# ── Groq LLM ──────────────────────────────────────────────────────────────────
def groq_contribution_idea(call_row, profile_text: str) -> tuple:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return "", "GROQ_API_KEY not set. Please export it and restart."

    prompt = (
        "You are an expert EU proposal writer helping a company craft a "
        "contribution idea for a Horizon Europe call.\n\n"
        "### CALL INFORMATION\n"
        f"Title: {call_row.get('title', '')}\n"
        f"Call Description: {call_row.get('call_description', '')}\n"
        f"Type of Action: {call_row.get('type_of_action', '')}\n"
        "### ORGANIZATION PROFILE\n"
        f"{profile_text}\n\n"
        "Write a well-structured contribution idea with these sections:\n"
        "1) Understanding of the Call — summarise the call's problem and objectives\n"
        "2) Relevance of the Company — why this organisation is a good fit or why is it not\n"
        "3) Proposed Technical Contributions — realistic and detailed contribution idea, constructed by keeping in mind the usability, effectiveness and relevancy of the solution\n"
        "4) Requirements — partners, resources, datasets"
    )

    try:
        resp = Groq(api_key=api_key).chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are an expert in EU research proposal writing."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.25,
            max_tokens=1100,
        )
        return prompt, resp.choices[0].message.content
    except Exception as e:
        return prompt, f"LLM error: {e}"

# ── Networking ────────────────────────────────────────────────────────────────
def suggest_keywords_from_profile(profile_text: str, top_k: int = 12) -> list:
    STOP = {
        "with", "that", "this", "from", "have", "been", "will", "their", "they",
        "also", "such", "more", "than", "which", "would", "into", "about", "other",
        "these", "some", "over",
    }
    tokens = [
        t for t in re.sub(r"[^a-zA-Z0-9\s\-]", " ", profile_text.lower()).split()
        if len(t) > 3 and t not in STOP
    ]
    return [w for w, _ in Counter(tokens).most_common(top_k)]

def add_networking_bookmark(user_id, title, link, event_date, notes):
    db_query(
        "INSERT INTO networking_bookmarks (user_id,title,link,event_date,notes,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (user_id, title, link, event_date, notes, now_iso()),
        write=True,
    )

def get_networking_bookmarks(user_id):
    return db_query(
        "SELECT id,title,link,event_date,notes,created_at "
        "FROM networking_bookmarks WHERE user_id=? ORDER BY created_at DESC",
        (user_id,)
    )

# ── Shared UI helpers ─────────────────────────────────────────────────────────
def _add_status_label(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["status_label"] = df["status"].map(resolve_status)
    return df

def _show_idea(call_row, profile_text: str):
    with st.spinner("Generating ..."):
        prompt, idea = groq_contribution_idea(call_row, profile_text)
    with st.expander("Inspect prompt sent to LLM"):
        st.text(prompt)
    st.markdown("### Contribution Idea")
    st.write(idea)

# ── Startup ───────────────────────────────────────────────────────────────────
init_db()
migrate_db()

# ── Pages ─────────────────────────────────────────────────────────────────────
def page_auth():
    st.header("Sign up / Login (Demo)")
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
            elif do_signup(email, name, pw):
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
                st.session_state.user = {
                    "id": user[0],
                    "email": user[1],
                    "name": user[2],
                }
                st.success(f"Welcome, {user[2]}!")
                time.sleep(0.6)
                st.rerun()
            else:
                st.error("Invalid credentials")

def page_upload():
    require_login()
    st.header("Upload Work Programme PDFs")

    uploaded = st.file_uploader(
        "Upload Horizon Europe Work Programme PDFs",
        type=["pdf"],
        accept_multiple_files=True,
    )
    if not uploaded:
        return

    existing_ids = get_existing_topic_ids()
    all_extracted = {}

    for pdf in uploaded:
        with st.spinner(f"Extracting IDs from {pdf.name} ..."):
            ids = extract_topic_ids_from_pdf(pdf)
        all_extracted[pdf.name] = ids
        st.success(f"**{pdf.name}** — found **{len(ids)}** topic IDs")
        if ids:
            with st.expander(f"Preview IDs from {pdf.name}"):
                st.write(ids)

    total_new = sum(1 for ids in all_extracted.values() for t in ids if t not in existing_ids)
    st.info(f"Total new (not yet in DB): **{total_new}** topic IDs across all files.")

    if not st.button("Fetch & Save Calls from API"):
        return

    rows_to_save, errors = [], []

    for filename, ids in all_extracted.items():
        new_ids = [t for t in ids if t not in existing_ids]
        if not new_ids:
            st.write(f"**{filename}**: all IDs already in DB, skipping.")
            continue

        progress = st.progress(0, text=f"Fetching {filename} ...")
        for i, tid in enumerate(new_ids):
            progress.progress((i + 1) / len(new_ids), text=f"Fetching {tid} ...")
            raw = fetch_call_by_topic_id(tid)
            if raw:
                rows_to_save.append(parse_topic_json(tid, raw))
            else:
                errors.append(tid)
            time.sleep(0.15)
        progress.empty()

    if rows_to_save:
        saved, failed = save_calls(rows_to_save)
        st.success(f"Saved **{saved}** calls to database.")
        for tid, err in failed:
            st.warning(f"Could not save {tid}: {err}")

    if errors:
        st.warning(
            f"Could not fetch **{len(errors)}** IDs from the API:\n\n"
            + "\n".join(f"- {e}" for e in errors[:30])
            + ("..." if len(errors) > 30 else "")
        )

def page_discover():
    require_login()
    st.header("Discover Calls & Shortlist")

    calls_df = load_calls_df()
    if calls_df.empty:
        st.warning("No calls in database. Please upload PDFs and fetch calls first.")
        return

    col1, col2, col3 = st.columns([1.2, 1, 1])
    q_tid = col1.text_input("Search by Topic ID (exact or partial)")
    show_status = col2.selectbox("Status", ["All", "Open", "Forthcoming", "Closed"])
    limit = col3.number_input("Show first N", 10, 1000, 50, 10)

    df = _add_status_label(calls_df)

    if q_tid.strip():
        df = df[df["topic_id"].str.contains(q_tid.strip(), case=False, na=False)]
    if show_status != "All":
        df = df[df["status_label"] == show_status]

    st.caption(f"Showing {min(len(df), int(limit))} of {len(df)} matching calls.")

    if df.empty:
        st.info("No matching calls found.")
        return

    df_show = df.head(int(limit))
    st.dataframe(
        df_show[["topic_id", "title", "deadline", "type_of_action", "status_label", "url"]],
        use_container_width=True,
    )

    st.subheader("Add to Interested")
    pick = st.selectbox("Select a call", df_show["topic_id"].tolist())
    if st.button("Add to Interested"):
        add_interested(st.session_state.user["id"], pick)
        st.success(f"Added **{pick}** to your Interested list.")

    if pick:
        row = df_show[df_show["topic_id"] == pick].iloc[0]
        with st.expander("Call Details"):
            st.markdown(f"**Title:** {row['title']}")
            st.markdown(f"**Deadline:** {row['deadline']}")
            st.markdown(f"**Type of Action:** {row['type_of_action']}")
            st.markdown(f"**Status:** {row['status_label']}")
            st.markdown(f"**URL:** [{row['url']}]({row['url']})")
            st.markdown("**Call Description:**")
            st.write(row["call_description"] or "*Not available*")

def page_interested():
    require_login()
    st.header("Interested Calls")

    df = get_interested_calls(st.session_state.user["id"])
    if df.empty:
        st.info("Your Interested list is empty. Add calls from Discover or Recommendations.")
        return

    df = _add_status_label(df)

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
        to_gen = st.selectbox("Generate idea for", df["topic_id"].tolist(), key="gen_interest")
        if st.button("Generate Contribution Idea"):
            profile_text, _ = load_org_profile(st.session_state.user["id"])
            if not profile_text:
                st.error("Please create your organization profile first.")
                return
            _show_idea(df[df["topic_id"] == to_gen].iloc[0], profile_text)

def page_profile():
    require_login()
    st.header("Organization Profile")

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
            with st.spinner("Embedding profile ..."):
                save_org_profile(st.session_state.user["id"], text.strip())
            st.success("Profile saved and embedded.")

def page_recommend():
    require_login()
    st.header("Recommendations")

    profile_text, profile_emb = load_org_profile(st.session_state.user["id"])
    if not profile_text:
        st.error("Please create your organization profile first.")
        return

    calls_df = load_calls_df()
    if calls_df.empty:
        st.warning("No calls in database. Please upload PDFs first.")
        return

    index = build_faiss_index(_df_hash(calls_df), calls_df)

    top_n = st.number_input("Top-N", 5, 100, 10, 1)
    if st.button("Find Matching Calls"):
        recs = recommend_top_n(profile_emb, int(top_n), calls_df, index)
        st.session_state.recs = recs if not recs.empty else pd.DataFrame()

    if "recs" not in st.session_state:
        return
    if st.session_state.recs.empty:
        st.info("No recommendations found.")
        return

    recs = _add_status_label(st.session_state.recs)

    st.dataframe(
        recs[
            ["topic_id", "title", "deadline", "type_of_action", "status_label", "similarity", "url"]
        ],
        use_container_width=True,
    )

    pick = st.selectbox("Add to Interested", recs["topic_id"].tolist())
    if st.button("Add Recommended"):
        add_interested(st.session_state.user["id"], pick)
        st.success("Added to Interested.")

    pick2 = st.selectbox("Generate idea for", recs["topic_id"].tolist(), key="gen_from_recs")
    if st.button("Generate Contribution Idea"):
        _show_idea(recs[recs["topic_id"] == pick2].iloc[0], profile_text)

def page_networking():
    require_login()
    st.header("Networking (B2B/Brokerage) — Demo")

    st.subheader("Curated External Links")
    cols = st.columns(3)
    for i, (name, link) in enumerate(CURATED_LINKS):
        cols[i % 3].link_button(name, link)

    profile_text, _ = load_org_profile(st.session_state.user["id"])

    st.subheader("Suggested Keywords")
    if profile_text:
        kws = suggest_keywords_from_profile(profile_text, top_k=12)
        st.write(", ".join(f"**{k}**" for k in kws))
        st.link_button(
            "Search on EEN with these keywords",
            "https://een.ec.europa.eu/partnering-opportunities?query=" + "+".join(kws[:6]),
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
        add_networking_bookmark(st.session_state.user["id"], title, link, str(event_date), notes)
        st.success("Bookmark saved!")

    st.subheader("My Bookmarks")
    rows = get_networking_bookmarks(st.session_state.user["id"])
    if rows:
        st.dataframe(
            pd.DataFrame(rows, columns=["id", "title", "link", "event_date", "notes", "created_at"])
            .drop(columns=["id"]),
            use_container_width=True,
        )
    else:
        st.info("No bookmarks yet.")

# ── Sidebar & routing ─────────────────────────────────────────────────────────
PAGES = {
    "Upload Work Programmes": page_upload,
    "Discover & Shortlist": page_discover,
    "Interested Calls": page_interested,
    "Recommendations": page_recommend,
    "Organization Profile": page_profile,
    "Networking": page_networking,
}

def main():
    st.session_state.setdefault("user", None)
    validate_session()

    st.sidebar.title("Horizon Europe")

    if not st.session_state.get("user"):
        page_auth()
        return

    st.sidebar.write(f"Signed in as: **{st.session_state.user['name']}**")
    if st.sidebar.button("Logout", use_container_width=True):
        st.session_state.user = None
        st.session_state.pop("recs", None)
        st.session_state.pop("_session_validated", None)
        st.rerun()

    page = st.sidebar.radio("Navigate", list(PAGES))
    PAGES[page]()

main()