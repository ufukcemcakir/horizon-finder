import json
import sqlite3
import threading
from typing import Any, Optional

from phase1_config import CFG, CALLS_COLS, _CALLS_PH, ACTIVE_STATUSES
from phase1_utils import _now_iso, is_allowed_action_type, _sha256


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
    CREATE TABLE IF NOT EXISTS screening_cache (
        profile_hash TEXT NOT NULL, topic_id TEXT NOT NULL,
        selected INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS screening_cache (
            profile_hash TEXT NOT NULL, topic_id TEXT NOT NULL,
            selected INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            PRIMARY KEY (profile_hash, topic_id)
        )
    """)
    conn.commit()

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
            (md.get('descriptionByte') if isinstance(md, dict) else '')
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


def load_calls_df(include_raw_json: bool = False):
    import pandas as pd

    if include_raw_json:
        cols, sql = list(CALLS_COLS), "SELECT * FROM horizon_calls"
    else:
        cols = [c for c in CALLS_COLS if c != "raw_json"]
        sql = f"SELECT {', '.join(cols)} FROM horizon_calls"
    rows = db_query(sql)
    return pd.DataFrame(rows, columns=cols) if rows else __import__('pandas').DataFrame(columns=cols)


def load_active_calls_df():
    import pandas as pd

    cols = [c for c in CALLS_COLS if c != "raw_json"]
    ph = ",".join(["?"] * len(__import__('builtins').__dict__.get('tuple', (1,)) ))
    # Simpler: use ACTIVE_STATUSES from config via import to avoid circular import
    rows = db_query(
        f"SELECT {', '.join(cols)} FROM horizon_calls WHERE LOWER(status) IN ({','.join(['?']*len(ACTIVE_STATUSES))})",
        tuple(s.lower() for s in ACTIVE_STATUSES),
    )
    df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    if not df.empty:
        from .phase1_utils import is_allowed_action_type

        df = df[df["type_of_action"].apply(is_allowed_action_type)].reset_index(drop=True)
    return df


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
    from .phase1_utils import _sha256

    row = db_query(
        "SELECT id,email,name,password_hash FROM users WHERE email=?",
        (email.strip().lower(),), fetch="one",
    )
    if row and row[3] == _sha256(password):
        return row[:3]
    return None


def save_org_profile(user_id: int, text: str) -> None:
    db_query(
        "INSERT OR REPLACE INTO org_profile (user_id,profile_text,updated_at) VALUES (?,?,?)",
        (user_id, text, _now_iso()), write=True,
    )


def load_org_profile(user_id: int) -> str:
    row = db_query("SELECT profile_text FROM org_profile WHERE user_id=?", (user_id,), fetch="one")
    return row[0] if row else ""


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


def get_interested_calls(user_id: int):
    import pandas as pd

    cols = [c for c in CALLS_COLS if c != "raw_json"]
    rows = db_query(
        f"SELECT {', '.join('hc.' + c for c in cols)} "
        "FROM user_interested_calls uic "
        "JOIN horizon_calls hc ON hc.topic_id = uic.topic_id "
        "WHERE uic.user_id = ? ORDER BY uic.created_at DESC",
        (user_id,),
    )
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


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


def load_cached_screening(phash: str) -> Optional[dict[str, bool]]:
    rows = db_query(
        "SELECT topic_id, selected FROM screening_cache WHERE profile_hash=?",
        (phash,),
    )
    if not rows:
        return None
    return {r[0]: bool(r[1]) for r in rows}


def save_screening_cache(phash: str, results: dict[str, bool]) -> None:
    now = _now_iso()
    db_query("DELETE FROM screening_cache WHERE profile_hash=?", (phash,), write=True)
    if results:
        rows = [(phash, tid, int(sel), now) for tid, sel in results.items()]
        db_executemany(
            "INSERT OR REPLACE INTO screening_cache (profile_hash, topic_id, selected, created_at) VALUES (?,?,?,?)",
            rows,
        )


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
