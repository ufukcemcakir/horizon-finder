import os
import json
import threading
from typing import Any, Optional

import psycopg2
import psycopg2.extras

from .phase1_config import CFG, CALLS_COLS, ACTIVE_STATUSES
from .phase1_utils import _now_iso, is_allowed_action_type, _sha256

_local = threading.local()


def _get_conn() -> psycopg2.extensions.connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            raise RuntimeError("Please set DATABASE_URL to a PostgreSQL DSN in the environment variable DATABASE_URL")
        conn = psycopg2.connect(db_url, connect_timeout=10)
        conn.autocommit = False
        _local.conn = conn
    return conn


def db_query(sql: str, params: tuple = (), *, fetch: str = "all", write: bool = False) -> Any:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        if write:
            conn.commit()
            cur.close()
            return None
        if fetch == "one":
            row = cur.fetchone()
            cur.close()
            return row
        else:
            rows = cur.fetchall()
            cur.close()
            return rows
    except Exception:
        conn.rollback()
        cur.close()
        raise


def init_db() -> None:
    conn = _get_conn()
    cur = conn.cursor()

    # try to enable pgvector if available
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        conn.commit()
    except Exception:
        conn.rollback()

    # Helper to execute create statements safely
    def safe_execute(stmt: str):
        try:
            cur.execute(stmt)
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False

    # Users
    safe_execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )

    # Org profile
    safe_execute(
        """
        CREATE TABLE IF NOT EXISTS org_profile (
            user_id INTEGER UNIQUE NOT NULL,
            profile_text TEXT NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    # Horizon calls: try with vector column first, fallback to without embedding
    created = safe_execute(
        """
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
            raw_json TEXT,
            cluster TEXT,
            budget TEXT,
            embedding vector(1536)
        )
        """
    )
    if not created:
        # try again without vector column
        safe_execute(
            """
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
                raw_json TEXT,
                cluster TEXT,
                budget TEXT
            )
            """
        )
    
    # Add cluster and budget columns if they don't exist (migration for existing tables)
    safe_execute("ALTER TABLE horizon_calls ADD COLUMN IF NOT EXISTS cluster TEXT;")
    safe_execute("ALTER TABLE horizon_calls ADD COLUMN IF NOT EXISTS budget TEXT;")

    # Other tables
    safe_execute(
        """
        CREATE TABLE IF NOT EXISTS user_interested_calls (
            user_id INTEGER NOT NULL,
            topic_id TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (user_id, topic_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(topic_id) REFERENCES horizon_calls(topic_id) ON DELETE CASCADE
        )
        """
    )

    safe_execute(
        """
        CREATE TABLE IF NOT EXISTS networking_bookmarks (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            title TEXT,
            link TEXT,
            event_date TEXT,
            notes TEXT,
            created_at TIMESTAMP WITH TIME ZONE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    safe_execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_cache (
            profile_hash TEXT NOT NULL,
            topic_id TEXT NOT NULL,
            score INTEGER,
            verdict TEXT,
            strengths TEXT,
            gaps TEXT,
            created_at TIMESTAMP WITH TIME ZONE,
            PRIMARY KEY (profile_hash, topic_id)
        )
        """
    )

    safe_execute(
        """
        CREATE TABLE IF NOT EXISTS screening_cache (
            profile_hash TEXT NOT NULL,
            topic_id TEXT NOT NULL,
            selected BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (profile_hash, topic_id)
        )
        """
    )

    safe_execute(
        """
        CREATE TABLE IF NOT EXISTS contributions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            topic_id TEXT NOT NULL,
            idea_text TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            UNIQUE (user_id, topic_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(topic_id) REFERENCES horizon_calls(topic_id) ON DELETE CASCADE
        )
        """
    )

    # Populate missing call_description fields from raw_json
    cur.execute(
        "SELECT topic_id, raw_json FROM horizon_calls WHERE call_description IS NULL OR call_description = ''"
    )
    rows = cur.fetchall()
    updates = []
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
    for desc, tid in updates:
        cur.execute("UPDATE horizon_calls SET call_description=%s WHERE topic_id=%s", (desc, tid))
    conn.commit()
    cur.close()


def db_executemany(sql: str, rows: list[tuple]) -> int:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.executemany(sql, rows)
        conn.commit()
        cur.close()
        return len(rows)
    except Exception:
        conn.rollback()
        cur.close()
        raise


def migrate_db() -> None:
    """Run any necessary migrations. Currently idempotent and calls init_db()."""
    try:
        init_db()
        # Populate cluster and budget from raw_json
        update_cluster_and_budget()
    except Exception:
        # Don't raise during app startup; log via stderr
        import sys

        print("Warning: migrate_db failed:", file=sys.stderr)
        import traceback

        traceback.print_exc()


def get_existing_topic_ids() -> set[str]:
    rows = db_query("SELECT topic_id FROM horizon_calls") or []
    return {r[0] for r in rows}


def save_calls(call_rows: list[dict]) -> tuple[int, list[tuple[str, str]]]:
    if not call_rows:
        return 0, []
    cols = list(CALLS_COLS)
    placeholders = ", ".join(["%s"] * len(cols))
    sql = (
        f"INSERT INTO horizon_calls ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT (topic_id) DO UPDATE SET " + ", ".join([f"{c}=EXCLUDED.{c}" for c in cols if c != 'topic_id'])
    )
    good: list[tuple] = []
    failed: list[tuple[str, str]] = []
    for row in call_rows:
        try:
            good.append(tuple(row.get(c) for c in cols))
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
        cols = list(CALLS_COLS)
        sql = "SELECT topic_id, title, call_description, summary, status, deadline, opening_date, type_of_action, programme_period, url, raw_json FROM horizon_calls"
    else:
        cols = [c for c in CALLS_COLS if c != "raw_json"]
        sql = f"SELECT {', '.join(cols)} FROM horizon_calls"
    rows = db_query(sql) or []
    return pd.DataFrame(rows, columns=cols) if rows else __import__('pandas').DataFrame(columns=cols)


def load_active_calls_df():
    import pandas as pd
    cols = [c for c in CALLS_COLS if c != "raw_json"]
    params = tuple(s.lower() for s in ACTIVE_STATUSES)
    placeholders = ", ".join(["%s"] * len(params))
    rows = db_query(
        f"SELECT {', '.join(cols)} FROM horizon_calls WHERE LOWER(status) IN ({placeholders})",
        params,
    ) or []
    df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    if not df.empty:
        df = df[df["type_of_action"].apply(is_allowed_action_type)].reset_index(drop=True)
    return df


def do_signup(email: str, name: str, password: str) -> Optional[tuple]:
    email = email.strip().lower()
    try:
        db_query(
            "INSERT INTO users (email,name,password_hash,created_at) VALUES (%s,%s,%s,%s)",
            (email, name.strip(), _sha256(password), _now_iso()), write=True,
        )
        return db_query("SELECT id,email,name FROM users WHERE email=%s", (email,), fetch="one")
    except Exception:
        return None


def do_login(email: str, password: str) -> Optional[tuple]:
    row = db_query(
        "SELECT id,email,name,password_hash FROM users WHERE email=%s",
        (email.strip().lower(),), fetch="one",
    )
    if row and row[3] == _sha256(password):
        return row[:3]
    return None


def save_org_profile(user_id: int, text: str) -> None:
    db_query(
        "INSERT INTO org_profile (user_id,profile_text,updated_at) VALUES (%s,%s,%s) ON CONFLICT (user_id) DO UPDATE SET profile_text=EXCLUDED.profile_text, updated_at=EXCLUDED.updated_at",
        (user_id, text, _now_iso()), write=True,
    )


def load_org_profile(user_id: int) -> str:
    row = db_query("SELECT profile_text FROM org_profile WHERE user_id=%s", (user_id,), fetch="one")
    return row[0] if row else ""


def add_interested(user_id: int, topic_id: str) -> None:
    try:
        db_query(
            "INSERT INTO user_interested_calls (user_id,topic_id,created_at) VALUES (%s,%s,%s) ON CONFLICT (user_id,topic_id) DO NOTHING",
            (user_id, topic_id, _now_iso()), write=True,
        )
    except Exception:
        pass


def remove_interested(user_id: int, topic_id: str) -> None:
    db_query(
        "DELETE FROM user_interested_calls WHERE user_id=%s AND topic_id=%s",
        (user_id, topic_id), write=True,
    )


def clear_interested(user_id: int) -> None:
    """Clear all calls from a user's shortlist"""
    db_query(
        "DELETE FROM user_interested_calls WHERE user_id=%s",
        (user_id,), write=True,
    )


def get_interested_calls(user_id: int):
    import pandas as pd
    cols = [c for c in CALLS_COLS if c != "raw_json"]
    select_cols = ", ".join([f"hc.{c}" for c in cols]) + ", (cns.id IS NOT NULL) as has_idea"
    rows = db_query(
        f"SELECT {select_cols} "
        "FROM user_interested_calls uic "
        "JOIN horizon_calls hc ON hc.topic_id = uic.topic_id "
        "LEFT JOIN contributions cns ON cns.user_id = uic.user_id AND cns.topic_id = hc.topic_id "
        "WHERE uic.user_id = %s ORDER BY uic.created_at DESC",
        (user_id,),
    ) or []
    cols_with_flag = cols + ["has_idea"]
    return pd.DataFrame(rows, columns=cols_with_flag) if rows else pd.DataFrame(columns=cols_with_flag)


def load_cached_analyses(phash: str, topic_ids: list[str]) -> dict[str, dict]:
    if not topic_ids:
        return {}
    ph = ",".join(["%s"] * len(topic_ids))
    rows = db_query(
        f"SELECT topic_id,score,verdict,strengths,gaps FROM analysis_cache "
        f"WHERE profile_hash=%s AND topic_id IN ({ph})",
        (phash, *topic_ids),
    ) or []
    return {
        r[0]: {"score": r[1], "verdict": r[2], "strengths": r[3], "gaps": r[4]}
        for r in (rows or [])
    }


def save_analysis(phash: str, tid: str, score: int, verdict: str, strengths: str, gaps: str) -> None:
    db_query(
        "INSERT INTO analysis_cache (profile_hash,topic_id,score,verdict,strengths,gaps,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (profile_hash, topic_id) DO UPDATE SET score=EXCLUDED.score, verdict=EXCLUDED.verdict, strengths=EXCLUDED.strengths, gaps=EXCLUDED.gaps, created_at=EXCLUDED.created_at",
        (phash, tid, score, verdict, strengths, gaps, _now_iso()), write=True,
    )


def load_cached_screening(phash: str) -> Optional[dict[str, bool]]:
    rows = db_query(
        "SELECT topic_id, selected FROM screening_cache WHERE profile_hash=%s",
        (phash,),
    ) or []
    if not rows:
        return None
    return {r[0]: bool(r[1]) for r in rows}


def save_screening_cache(phash: str, results: dict[str, bool]) -> None:
    now = _now_iso()
    db_query("DELETE FROM screening_cache WHERE profile_hash=%s", (phash,), write=True)
    if results:
        rows = [(phash, tid, bool(sel), now) for tid, sel in results.items()]
        db_executemany(
            "INSERT INTO screening_cache (profile_hash, topic_id, selected, created_at) VALUES (%s,%s,%s,%s)",
            rows,
        )


def add_networking_bookmark(user_id: int, title: str, link: str, event_date: str, notes: str) -> None:
    db_query(
        "INSERT INTO networking_bookmarks (user_id,title,link,event_date,notes,created_at) VALUES (%s,%s,%s,%s,%s,%s)",
        (user_id, title, link, event_date, notes, _now_iso()), write=True,
    )


def get_networking_bookmarks(user_id: int) -> list[tuple]:
    return db_query(
        "SELECT id,title,link,event_date,notes,created_at "
        "FROM networking_bookmarks WHERE user_id=%s ORDER BY created_at DESC",
        (user_id,),
    ) or []


def save_contribution(user_id: int, topic_id: str, idea_text: str) -> None:
    db_query(
        "INSERT INTO contributions (user_id, topic_id, idea_text, created_at) VALUES (%s,%s,%s,%s) ON CONFLICT (user_id, topic_id) DO UPDATE SET idea_text=EXCLUDED.idea_text, created_at=EXCLUDED.created_at",
        (user_id, topic_id, idea_text, _now_iso()), write=True,
    )


def get_contribution(user_id: int, topic_id: str) -> Optional[str]:
    row = db_query(
        "SELECT idea_text FROM contributions WHERE user_id=%s AND topic_id=%s",
        (user_id, topic_id), fetch="one",
    )
    return row[0] if row else None


def extract_cluster_and_budget_from_json(raw_json_str: str) -> tuple[Optional[str], Optional[str]]:
    """Extract cluster and budget info from raw_json."""
    if not raw_json_str:
        return None, None
    try:
        raw = json.loads(raw_json_str)
    except (json.JSONDecodeError, TypeError):
        return None, None
    
    # Extract cluster (can be in metadata or under different keys)
    cluster = None
    budget = None
    
    metadata = raw.get('metadata') or {}
    if isinstance(metadata, dict):
        cluster = metadata.get('cluster') or metadata.get('clusterName')
        budget = metadata.get('budget') or metadata.get('fundingAmount')
    
    # Try top-level keys
    if not cluster:
        cluster = raw.get('cluster') or raw.get('clusterName')
    if not budget:
        budget = raw.get('budget') or raw.get('fundingAmount') or raw.get('budgetAmount')
    
    return cluster, budget


def update_cluster_and_budget():
    """Populate cluster and budget columns from raw_json for all calls."""
    try:
        rows = db_query("SELECT topic_id, raw_json FROM horizon_calls WHERE cluster IS NULL OR budget IS NULL") or []
        for topic_id, raw_json_str in rows:
            cluster, budget = extract_cluster_and_budget_from_json(raw_json_str)
            if cluster or budget:
                db_query(
                    "UPDATE horizon_calls SET cluster=%s, budget=%s WHERE topic_id=%s",
                    (cluster, budget, topic_id),
                    write=True
                )
    except Exception:
        pass  # Silently fail if this is an optional operation
