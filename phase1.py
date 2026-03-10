# ============================================================
#   Horizon Europe Demo Platform (Simplified & Fast Version)
# ============================================================

import os
import re
import json
import sqlite3
import pdfplumber
import numpy as np
import pandas as pd
import streamlit as st
import requests
import faiss

from sentence_transformers import SentenceTransformer
from groq import Groq


# ============================================================
# SETTINGS
# ============================================================

PDF_FOLDER = "pdfs"
DB_PATH = "horizon.db"
API_KEY = "SEDIA"
EMBEDDING_MODEL = "sentence-transformers/all-mpnet-base-v2"

model = SentenceTransformer(EMBEDDING_MODEL)


# ============================================================
# DATABASE
# ============================================================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Calls table with PRIMARY KEY
    c.execute("""
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
        raw_json TEXT,
        embedding BLOB
    );
    """)

    # Org profile (single row)
    c.execute("""
    CREATE TABLE IF NOT EXISTS org_profile (
        id INTEGER PRIMARY KEY,
        profile_text TEXT,
        embedding BLOB
    );
    """)

    # Interested calls
    c.execute("""
    CREATE TABLE IF NOT EXISTS interested_calls (
        topic_id TEXT PRIMARY KEY
    );
    """)

    conn.commit()
    conn.close()

init_db()


# ============================================================
# FETCH HELPERS
# ============================================================

TOPIC_REGEX = r"\bHORIZON-[A-Z0-9]+-\d{4}-[\w-]+-\d{1,2}(?:-\d{1,2})?\b"

def _norm(text):
    if not text:
        return ""
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    return text.replace("\n", " ")

def extract_ids_from_pdf(path):
    ids = set()
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = _norm(page.extract_text())
            for m in re.findall(TOPIC_REGEX, text):
                ids.add(m)
    return sorted(ids)

def fetch_call_info(tid):
    url = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
    params = {"apiKey": API_KEY, "text": f"\"{tid}\""}

    files = {
        "languages": ("blob", json.dumps(["en"]), "application/json"),
        "pageSize": (None, "1"),
        "pageNumber": (None, "1"),
    }

    try:
        r = requests.post(url, params=params, files=files, timeout=30)
        data = r.json()
        if not data.get("results"):
            return None
        return data["results"][0]
    except:
        return None


# ============================================================
# DB OPERATIONS
# ============================================================

def save_call(row):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO horizon_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        row["topic_id"], row["title"], row["scope"], row["expected_outcomes"],
        row["summary"], row["status"], row["deadline"], row["opening_date"],
        row["type_of_action"], row["programme_period"], row["url"],
        json.dumps(row["raw_json"]), row["embedding"]
    ))
    conn.commit()
    conn.close()

def load_call(tid):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT * FROM horizon_calls WHERE topic_id=?", (tid,)).fetchone()
    conn.close()
    return row

def load_all_calls():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT * FROM horizon_calls").fetchall()
    conn.close()
    return rows

def add_interested(tid):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR IGNORE INTO interested_calls VALUES (?)", (tid,))
    conn.commit()
    conn.close()

def load_interested():
    conn = sqlite3.connect(DB_PATH)
    ids = [r[0] for r in conn.execute("SELECT topic_id FROM interested_calls")]
    conn.close()
    return ids

def save_profile(text, emb):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM org_profile")
    conn.execute("INSERT INTO org_profile (profile_text, embedding) VALUES (?,?)",
                 (text, emb.tobytes()))
    conn.commit()
    conn.close()

def load_profile():
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT profile_text, embedding FROM org_profile").fetchone()
    conn.close()
    if not row:
        return None, None
    return row[0], np.frombuffer(row[1], dtype=np.float32)


# ============================================================
# VECTOR SEARCH
# ============================================================

def build_index(calls):
    if not calls:
        return None, None

    texts = []
    for r in calls:
        title, scope, outcomes = r[1], r[2], r[3]
        txt = f"{title}\n{scope}\n{outcomes}"
        texts.append(txt)

    emb = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)
    return index, emb


# ============================================================
# LLM
# ============================================================

def generate_offer(call, org_profile):
    client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

    info = f"""
Title: {call['title']}
Scope: {call['scope']}
Expected Outcomes: {call['expected_outcomes']}
Deadline: {call['deadline']}
URL: {call['url']}
"""

    prompt = f"""
You are an EU proposal expert.

CALL INFORMATION:
{info}

ORGANIZATION PROFILE:
{org_profile}

Your job is to generate a **Contribution Offer** for a Horizon Europe call,
based on the call description and the company's capabilities.

Follow this structure:

1. **Understanding of the Call**
   Give the call's name and then summarize what the call is about.

2. **Relevance of the Company**
   Assess whether the company is suited to contribute to the call and explain your reasoning.

3. **Proposed Technical Contributions**
   If you assess that the company can contribute to the call,
   provide detailed technical contributions the company can make. Keep it
   concise and realistic. Clearly evaluate and explain the inputs, outputs and
   the relevance to the call.
"""

    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=800,
        temperature=0.25
    )

    return resp.choices[0].message.content


# ============================================================
# PRELOAD PDFs ONCE
# ============================================================

def preload_pdfs():
    if not os.path.exists(PDF_FOLDER):
        return

    pdfs = [f for f in os.listdir(PDF_FOLDER) if f.endswith(".pdf")]
    for f in pdfs:
        ids = extract_ids_from_pdf(os.path.join(PDF_FOLDER, f))
        for tid in ids:
            if load_call(tid):
                continue
            call = fetch_call_info(tid)
            if call:
                txt = f"{call.get('title')}\n{call.get('scope')}\n{call.get('expectedOutcomes')}"
                emb = model.encode([txt], convert_to_numpy=True, normalize_embeddings=True)[0]

                row = {
                    "topic_id": tid,
                    "title": call.get("title"),
                    "scope": call.get("scope"),
                    "expected_outcomes": call.get("expectedOutcomes"),
                    "summary": call.get("summary"),
                    "status": call.get("status"),
                    "deadline": call.get("deadlineDate"),
                    "opening_date": call.get("openingDate"),
                    "type_of_action": call.get("typesOfAction"),
                    "programme_period": call.get("ProgrammePeriod"),
                    "url": call.get("url"),
                    "raw_json": call,
                    "embedding": emb.tobytes(),
                }
                save_call(row)


if "preloaded" not in st.session_state:
    preload_pdfs()
    st.session_state["preloaded"] = True


# ============================================================
# STREAMLIT UI
# ============================================================

st.title("Horizon Europe Demo")


pages = ["Calls", "Profile", "Match"]
page = st.sidebar.radio("Menu", pages)


# ============================================================
# PAGE 1 — CALLS
# ============================================================

if page == "Calls":
    st.header("Search Calls")

    tid = st.text_input("Topic ID")
    if tid:
        row = load_call(tid)
        if row:
            st.write(pd.DataFrame([row], columns=[
                "topic_id","title","scope","expected_outcomes","summary","status",
                "deadline","opening_date","type_of_action","programme_period",
                "url","raw_json","embedding"
            ]))

            if st.button("Add to Interested"):
                add_interested(tid)
                st.success("Added!")

    st.subheader("All Calls")
    all_calls = load_all_calls()
    if all_calls:
        df = pd.DataFrame(all_calls, columns=[
            "topic_id","title","scope","expected_outcomes","summary","status",
            "deadline","opening_date","type_of_action","programme_period",
            "url","raw_json","embedding"
        ]).drop(columns=["embedding"])
        st.dataframe(df, use_container_width=True)

    st.subheader("Interested Calls")
    st.write(load_interested())


# ============================================================
# PAGE 2 — PROFILE
# ============================================================

elif page == "Profile":
    st.header("Organization Profile")

    txt, _emb = load_profile()

    new_txt = st.text_area("Profile", value=txt or "")

    if st.button("Save"):
        eb = model.encode([new_txt], convert_to_numpy=True, normalize_embeddings=True)[0]
        save_profile(new_txt, eb)
        st.success("Saved!")


# ============================================================
# PAGE 3 — MATCH
# ============================================================

elif page == "Match":
    st.header("Match Calls")

    profile_txt, profile_emb = load_profile()
    if not profile_txt:
        st.error("No profile found.")
        st.stop()

    # Interested calls
    st.subheader("Interested Calls")
    interested_ids = load_interested()
    st.write(interested_ids)

    # Top-N
    st.subheader("Top Matches")
    top_n = st.number_input("Top N", 1, 50, 5)

    calls = load_all_calls()
    index, emb = build_index(calls)

    if st.button("Find Matches"):
        D, I = index.search(profile_emb.reshape(1, -1), int(top_n))
        st.session_state["matches"] = [calls[i] for i in I[0]]

    if "matches" in st.session_state:
        for r in st.session_state["matches"]:
            tid = r[0]

            st.markdown(f"### {r[1]}")
            st.write(f"ID: {tid}")
            st.write(f"Deadline: {r[6]}")

            if st.button(f"Add {tid}", key=f"add_{tid}"):
                add_interested(tid)
                st.success("Added")

            if st.button(f"Offer {tid}", key=f"offer_{tid}"):
                call = {
                    "topic_id": tid,
                    "title": r[1],
                    "scope": r[2],
                    "expected_outcomes": r[3],
                    "deadline": r[6],
                    "url": r[10],
                }
                offer = generate_offer(call, profile_txt)
                st.markdown("### Offer")
                st.write(offer)