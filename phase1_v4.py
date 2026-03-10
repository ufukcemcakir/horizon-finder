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

DB_PATH        = "horizon.db"
API_KEY        = "SEDIA"
FT_SEARCH_BASE = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"

CALLS_COLS = [
    "topic_id", "title", "call_description", "summary", "status", "deadline",
    "opening_date", "type_of_action", "programme_period", "url", "raw_json",
]
_CALLS_PLACEHOLDERS = ",".join("?" * len(CALLS_COLS))  # derived once, never hand-counted

STATUS_MAP = {
    "31094501": "Open", "31094502": "Forthcoming", "31094503": "Closed",
    "open":     "Open", "forthcoming": "Forthcoming", "closed": "Closed",
}

CURATED_LINKS = [
    ("Funding &amp; Tenders Portal",     "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/browse-by-programme"),
    ("Ideal-ist (Digital/ICT)",         "https://www.ideal-ist.eu/"),
    ("Enterprise Europe Network",       "https://een.ec.europa.eu/"),
    ("EEN Partnering Opportunities",    "https://een.ec.europa.eu/partnering-opportunities"),
    ("Horizon Europe NCP Networks",     "https://www.ncpportal.eu/"),
    ("EUREKA Network",                  "https://www.eurekanetwork.org/"),
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
        profile_hash TEXT,
        topic_id     TEXT,
        score        INTEGER,
        verdict      TEXT,
        strengths    TEXT,
        gaps         TEXT,
        created_at   TEXT,
        PRIMARY KEY (profile_hash, topic_id)
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

    # Old schema: merge scope + expected_outcomes into call_description
    if "scope" in cols and "call_description" not in cols:
        conn.execute("ALTER TABLE horizon_calls ADD COLUMN call_description TEXT DEFAULT ''")
        conn.execute("""
            UPDATE horizon_calls SET call_description =
                COALESCE(NULLIF(scope, ''), '')
                || CASE WHEN scope != '' AND expected_outcomes != ''
                        THEN char(10) || char(10) ELSE '' END
                || COALESCE(NULLIF(expected_outcomes, ''), '')
        """)
        conn.commit()

    # Backfill rows still missing call_description from raw_json
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
    """Return first element of a list/tuple, or the value itself."""
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    return v

def _sg(dct, key, default=None):
    """Safe dict.get — returns default if dct is not a dict."""
    return dct.get(key, default) if isinstance(dct, dict) else default

def _pf(dct, key):
    """Safe-get + pick-first in one call."""
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
    """
    Extract call description from metadata.descriptionByte.
    """
    md  = _sg(result_item, "metadata") or {}
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
    """
    Fetch a single call from the F&T Search API.
    Strategy A (quoted text) mirrors the working notebook and is tried first.
    Strategy B (unquoted, wider net) is the fallback.
    """
    # Strategy A: quoted text — precise match
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

    # Strategy B: unquoted fallback with validation
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
                _pf(r,  "identifier"), _pf(r,  "callIdentifier"), _pf(r,  "reference"),
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

    call_id = _sg(ri, "callIdentifier")
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
    """
    Bulk-insert call rows. Returns (saved_count, failed_list).
    No Streamlit calls — UI responsibility stays with the caller.
    """
    if not call_rows:
        return 0, []
    conn   = get_conn()
    saved  = 0
    failed = []
    sql    = f"INSERT OR REPLACE INTO horizon_calls VALUES ({_CALLS_PLACEHOLDERS})"
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
    """Load calls. Excludes raw_json by default (large field, not needed for UI)."""
    if include_raw_json:
        cols, sql = CALLS_COLS, "SELECT * FROM horizon_calls"
    else:
        cols = [c for c in CALLS_COLS if c != "raw_json"]
        sql  = f"SELECT {', '.join(cols)} FROM horizon_calls"
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
        return db_query("SELECT id,email,name FROM users WHERE email=?", (email,), fetch="one")
    except sqlite3.IntegrityError:
        return None

def do_login(email: str, password: str):
    row = db_query(
        "SELECT id,email,name,password_hash FROM users WHERE email=?",
        (email.strip().lower(),),
        fetch="one",
    )
    if row and row[3] == sha256(password):
        return row[:3]
    return None

def validate_session():
    """
    Clear session if the stored user no longer exists in the DB.
    Skips the DB round-trip on subsequent renders within the same Streamlit run.
    """
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
    """Stable 16-char key representing a specific profile text (for cache keying)."""
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
            (user_id, topic_id, now_iso()),
            write=True,
        )
    except sqlite3.IntegrityError:
        pass

def remove_interested(user_id: int, topic_id: str):
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
    return pd.DataFrame(rows, columns=cols)

# ── FAISS shortlisting (Stage 1 of Call Analyzer) ────────────────────────────

def load_active_calls_df() -> pd.DataFrame:
    """
    Load only Open and Forthcoming calls for use in recommendations.
    Closed calls are excluded — they are still browsable in Discover & Shortlist.
    Status codes per the API: 31094501=Open, 31094502=Forthcoming, 31094503=Closed.
    """
    cols = [c for c in CALLS_COLS if c != "raw_json"]
    active_statuses = ("31094501", "31094502", "open", "forthcoming", "Open", "Forthcoming")
    rows = db_query(
        f"SELECT {', '.join(cols)} FROM horizon_calls "
        f"WHERE LOWER(status) IN ({','.join(['?']*len(active_statuses))})",
        tuple(s.lower() for s in active_statuses),
    )
    return pd.DataFrame(rows, columns=cols)

def _df_hash(df: pd.DataFrame) -> str:
    return hashlib.md5(pd.util.hash_pandas_object(df).values.tobytes()).hexdigest()

def _get_or_build_index(active_df: pd.DataFrame):
    """
    Return a FAISS index for active_df, building it only when the data has changed.
    Result is stored in st.session_state["_faiss_index"] keyed by a hash of the data.
    """
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
    """
    Stage 1: retrieve the top-k semantically similar calls via FAISS.
    Returns a DataFrame with an added ‘semantic_score’ column.
    """
    if index is None or active_df.empty:
        return pd.DataFrame()
    D, I = index.search(profile_emb.reshape(1, -1), min(int(k), len(active_df)))
    shortlist = active_df.iloc[I[0]].copy()
    shortlist["semantic_score"] = D[0]
    return shortlist.sort_values("semantic_score", ascending=False)

# ── Analysis cache helpers ────────────────────────────────────────────────────

def load_cached_analyses(phash: str, topic_ids: list) -> dict:
    """
    Returns {topic_id: {score, verdict, strengths, gaps}}
    """
    if not topic_ids:
        return {}
    placeholders = ",".join("?" * len(topic_ids))
    rows = db_query(
        f"SELECT topic_id, score, verdict, strengths, gaps FROM analysis_cache "
        f"WHERE profile_hash=? AND topic_id IN ({placeholders})",
        (phash, *topic_ids),
    )
    return {
        r[0]: {"score": r[1], "verdict": r[2], "strengths": r[3], "gaps": r[4]}
        for r in (rows or [])
    }

def save_analysis(phash: str, topic_id: str, score: int,
                  verdict: str, strengths: str, gaps: str):
    db_query(
        "INSERT OR REPLACE INTO analysis_cache "
        "(profile_hash, topic_id, score, verdict, strengths, gaps, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (phash, topic_id, score, verdict, strengths, gaps, now_iso()),
        write=True,
    )

# ── Call Analyzer Agent (Stage 2) ─────────────────────────────────────────────

_ANALYZER_SYSTEM = """
You are a senior EU funding advisor. Your task is to evaluate how well a Horizon Europe
call matches a specific organization’s profile and capabilities.

You MUST respond with a single valid JSON object and nothing else — no markdown fences,
no explanation outside the JSON.

Required JSON schema:
{
    “score”: <integer 0-100>,
    “verdict”: “<one sentence: why this call is or is not a strong fit>”,
    “strengths”: ["<strength 1>", "<strength 2>"],
    “gaps”: ["<gap or missing capability 1>"]
}

Scoring guide:
80-100  Strong fit — org capabilities map directly to the call objectives
60-79   Good fit — relevant expertise with minor gaps
40-59   Partial fit — some overlap but significant gaps
20-39   Weak fit — tangential relevance only
0-19    Not a fit
"""

def analyze_call(call_row, profile_text: str, api_key: str) -> dict:
    """
    Single-call LLM analysis.
    """
    prompt = (
        "### ORGANIZATION PROFILE\n"
        f"{profile_text}\n\n"
        "### CALL TO EVALUATE\n"
        f"Title: {call_row.get('title', '')}\n"
        f"Type of Action: {call_row.get('type_of_action', '')}\n"
        f"Description:\n{call_row.get('call_description', '') or call_row.get('summary', '')}\n"
    )
    try:
        resp = Groq(api_key=api_key).chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": _ANALYZER_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.0,
            max_tokens=400,
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
        return {"score": -1, "verdict": f"JSON parse error: {e} | raw: {raw[:200]}", "strengths": [], "gaps": []}
    except Exception as e:
        return {"score": -1, "verdict": f"LLM error: {e}", "strengths": [], "gaps": []}

def run_call_analyzer(shortlist: pd.DataFrame, profile_text: str,
                      phash: str, n_analyze: int, progress_cb=None) -> pd.DataFrame:
    """
    Stage 2: run the Call Analyzer Agent on the top-n_analyze rows.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        st.error("GROQ_API_KEY not set. The agent cannot run without it.")
        return pd.DataFrame()

    candidates = shortlist.head(int(n_analyze)).copy()
    topic_ids  = candidates["topic_id"].tolist()

    cached = load_cached_analyses(phash, topic_ids)

    results = {}
    to_fetch = [tid for tid in topic_ids if tid not in cached]
    total    = len(to_fetch)

    for i, tid in enumerate(to_fetch):
        if progress_cb:
            progress_cb(i, total, tid)
        row     = candidates[candidates["topic_id"] == tid].iloc[0]
        outcome = analyze_call(row, profile_text, api_key)
        save_analysis(
            phash, tid, outcome["score"], outcome["verdict"],
            json.dumps(outcome["strengths"]),
            json.dumps(outcome["gaps"]),
        )
        results[tid] = outcome

    all_results = {**cached, **results}

    for tid, v in all_results.items():
        for key in ("strengths", "gaps"):
            if isinstance(v[key], str):
                try:
                    v[key] = json.loads(v[key])
                except Exception:
                    v[key] = [v[key]]

    candidates["llm_score"]  = candidates["topic_id"].map(lambda t: all_results.get(t, {}).get("score", -1))
    candidates["verdict"]    = candidates["topic_id"].map(lambda t: all_results.get(t, {}).get("verdict", ""))
    candidates["strengths"]  = candidates["topic_id"].map(lambda t: all_results.get(t, {}).get("strengths", []))
    candidates["gaps"]       = candidates["topic_id"].map(lambda t: all_results.get(t, {}).get("gaps", []))

    return candidates.sort_values("llm_score", ascending=False).reset_index(drop=True)

# ── Groq LLM (contribution idea — unchanged) ──────────────────────────────────

def groq_contribution_idea(call_row, profile_text: str) -> tuple:
    """Returns (prompt, response)."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return "", "GROQ_API_KEY not set. Please export it and restart."

    prompt = (
        "You are an expert EU proposal writer helping a company craft a concise "
        "contribution idea for a Horizon Europe call.\n\n"
        "### CALL INFORMATION\n"
        f"Title: {call_row.get('title', '')}\n"
        f"Call Description: {call_row.get('call_description', '')}\n"
        f"Type of Action: {call_row.get('type_of_action', '')}\n"
        f"Deadline: {call_row.get('deadline', '')}\n"
        f"URL: {call_row.get('url', '')}\n\n"
        "### ORGANIZATION PROFILE\n"
        f"{profile_text}\n\n"
        "Write a well-structured contribution idea with these sections:\n"
        "1) Understanding of the Call — summarise the call's problem and objectives\n"
        "2) Relevance of the Company — why this organisation is a good fit\n"
        "3) Proposed Technical Contributions — concrete tasks/work packages\n"
        "4) Requirements — partners, resources, TRL level needed"
    )
    try:
        resp = Groq(api_key=api_key).chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are an expert in EU research proposal writing."},
                {"role": "user",   "content": prompt},
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
        "also", "such", "more", "than", "which", "would", "into", "about",
        "other", "these", "some", "over",
    }
    tokens = [
        t for t in re.sub(r"[^a-zA-Z0-9\s-]", " ", profile_text.lower()).split()
        if len(t) > 3 and t not in STOP
    ]
    return [w for w, _ in Counter(tokens).most_common(top_k)]

def add_networking_bookmark(user_id, title, link, event_date, notes):
    db_query(
        "INSERT INTO networking_bookmarks (user_id,title,link,event_date,notes,created_at) VALUES (?,?,?,?,?,?)",
        (user_id, title, link, event_date, notes, now_iso()),
        write=True,
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

def _show_idea(call_row, profile_text: str):
    with st.spinner("Generating …"):
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
            name  = st.text_input("Name")
            pw    = st.text_input("Password", type="password")
            pw2   = st.text_input("Confirm Password", type="password")
            ok    = st.form_submit_button("Create account")
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
            pw    = st.text_input("Password", type="password", key="login_pw")
            ok    = st.form_submit_button("Login")
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
    st.header("Upload Work Programme PDFs")

    uploaded = st.file_uploader(
        "Upload Horizon Europe Work Programme PDFs",
        type=["pdf"], accept_multiple_files=True,
    )
    if not uploaded:
        return

    existing_ids  = get_existing_topic_ids()
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
    q_tid       = col1.text_input("Search by Topic ID (exact or partial)")
    show_status = col2.selectbox("Status", ["All", "Open", "Forthcoming", "Closed"])
    limit       = col3.number_input("Show first N", 10, 1000, 50, 10)

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
    st.header("Recommendations — Call Analyzer Agent")

    profile_text, profile_emb = load_org_profile(st.session_state.user["id"])
    if not profile_text:
        st.error("Please create your organization profile first.")
        return

    active_df = load_active_calls_df()
    if active_df.empty:
        st.warning(
            "No Open or Forthcoming calls in the database. "
            "Closed calls are only shown in Discover & Shortlist. "
            "Please upload a new work programme PDF to fetch active calls."
        )
        return

    phash = profile_hash(profile_text)

    st.subheader("Stage 1 — Semantic Shortlisting")
    st.caption(
        f"Working with **{len(active_df)} Open / Forthcoming** calls. "
        "FAISS retrieves an initial candidate pool using embedding similarity. "
        "The agent then analyzes those candidates in Stage 2."
    )
    col1, col2 = st.columns(2)
    pool_size = col1.number_input(
        "Candidate pool (FAISS top-K)",
        min_value=5, max_value=min(200, len(active_df)), value=min(40, len(active_df)), step=5,
        help="How many calls FAISS pre-selects before the LLM agent runs.",
    )
    n_analyze = col2.number_input(
        "Calls to analyze with Agent",
        min_value=1, max_value=min(30, int(pool_size)), value=min(10, int(pool_size)), step=1,
        help="How many of the top-K the LLM agent will score.",
    )

    if st.button("Run Stage 1: Shortlist", type="primary"):
        with st.spinner("Building semantic index over active calls …"):
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
    st.success(f"Shortlisted **{len(shortlist)}** candidates by semantic similarity.")
    with st.expander("View shortlist (semantic scores)"):
        st.dataframe(
            shortlist_display[[
                "topic_id", "title", "deadline", "status_label", "semantic_score",
            ]].rename(columns={"semantic_score": "sem_score"}),
            use_container_width=True,
        )

    st.divider()

    st.subheader("Stage 2 — Call Analyzer Agent")
    st.caption(
        "The agent sends each candidate's call description + your organization profile "
        "to an LLM. It returns a score, verdict, strengths, and gaps."
    )

    cached_count = len(load_cached_analyses(phash, shortlist.head(int(n_analyze))["topic_id"].tolist()))
    fresh_count  = int(n_analyze) - cached_count
    if fresh_count > 0:
        st.info(
            f"**{cached_count}** of the top {n_analyze} calls are already cached. "
            f"**{fresh_count}** will be sent (~{fresh_count * 3}s)."
        )
    else:
        st.success(f"All {n_analyze} calls already cached — instant results.")

    if st.button("Run Stage 2: Analyze with Agent", type="primary"):
        progress_bar = st.progress(0, text="Starting agent ...")
        status_text  = st.empty()

        def _progress(i, total, tid):
            if total == 0:
                return
            progress_bar.progress(i / total, text=f"Analyzing {i+1}/{total}: {tid}")
            status_text.caption(f"Querying LLM for **{tid}** ...")

        with st.spinner("Agent running ..."):
            agent_recs = run_call_analyzer(
                shortlist, profile_text, phash, int(n_analyze), _progress
            )

        progress_bar.empty()
        status_text.empty()
        st.session_state.agent_recs = agent_recs if not agent_recs.empty else pd.DataFrame()

    if "agent_recs" not in st.session_state or st.session_state.agent_recs is None:
        return
    if st.session_state.agent_recs.empty:
        st.warning("Agent returned no results. Check GROQ_API_KEY.")
        return

    st.divider()

    agent_recs = _add_status_label(st.session_state.agent_recs)
    st.subheader("Agent Analysis Results")

    display_df = agent_recs[[
        "topic_id", "title", "deadline", "type_of_action",
        "status_label", "llm_score", "verdict",
    ]].rename(columns={"llm_score": "score (0-100)"})
    st.dataframe(display_df, use_container_width=True)

    st.subheader("Detailed Assessments")
    for _, row in agent_recs.iterrows():
        score = row["llm_score"]
        color = "🟢" if score >= 70 else "🟡" if score >= 45 else "🔴"
        with st.expander(f"{color} **{score}/100** — {row['title'][:90]}"):
            st.markdown(f"**Topic ID:** `{row['topic_id']}`")
            st.markdown(f"**Deadline:** {row['deadline']}  |  **Type:** {row['type_of_action']}")
            st.markdown(f"**URL:** [{row['url']}]({row['url']})")
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
            st.markdown("**Call Description:**")
            st.write(row.get("call_description") or "*Not available*")

    st.divider()

    valid_recs = agent_recs[agent_recs["llm_score"] >= 0]
    if valid_recs.empty:
        return

    col_a, col_b = st.columns(2)
    with col_a:
        pick = st.selectbox("Add to Interested", valid_recs["topic_id"].tolist(), key="agent_add")
        if st.button("Add to Interested"):
            add_interested(st.session_state.user["id"], pick)
            st.success("Added to Interested.")
    with col_b:
        pick2 = st.selectbox("Generate idea for", valid_recs["topic_id"].tolist(), key="agent_idea")
        if st.button("Generate Contribution Idea"):
            _show_idea(valid_recs[valid_recs["topic_id"] == pick2].iloc[0], profile_text)

    if st.button("Clear agent cache for this profile"):
        db_query("DELETE FROM analysis_cache WHERE profile_hash=?", (phash,), write=True)
        st.session_state.agent_recs = None
        st.success("Cache cleared. Next run will re-analyze all calls.")
        st.rerun()

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
        title      = st.text_input("Event Title")
        link       = st.text_input("Event Link (URL)")
        event_date = st.date_input("Event Date")
        notes      = st.text_area("Notes", "")
        ok         = st.form_submit_button("Save Bookmark")
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
    "Discover & Shortlist":   page_discover,
    "Interested Calls":       page_interested,
    "Recommendations":        page_recommend,
    "Organization Profile":   page_profile,
    "Networking":             page_networking,
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
        for k in ("shortlist", "agent_recs", "_session_validated"):
            st.session_state.pop(k, None)
        st.rerun()

    page = st.sidebar.radio("Navigate", list(PAGES))
    PAGES[page]()

main()