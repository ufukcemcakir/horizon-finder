import math
import json
from collections import Counter
import re
import pandas as pd

from .phase1_config import CFG

_PREFILTER_STOP_WORDS: frozenset[str] = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "has",
    "her", "was", "one", "our", "out", "its", "his", "how", "may", "who",
    "did", "get", "let", "say", "she", "too", "use", "with", "that", "this",
    "from", "have", "been", "will", "their", "they", "also", "such", "more",
    "than", "which", "would", "into", "about", "other", "these", "some",
    "over", "each", "only", "very", "when", "what", "your", "most", "make",
    "like", "just", "well", "back", "much", "then", "them", "come", "made",
    "find", "here", "many", "give", "take", "being", "where", "could",
    "after", "should", "shall", "through", "between", "those", "there",
    "based", "using", "including", "particular", "specific", "related",
    "within", "across", "towards", "ensure", "support", "develop",
    "development", "research", "innovation", "project", "projects",
    "action", "actions", "horizon", "europe", "european", "call", "calls",
    "proposal", "proposals", "expected", "outcomes", "topic", "organization",
    "company", "sme", "enterprise", "institute", "institutional",
})

_RE_WORD_SPLIT = re.compile(r"[^a-z0-9]+")


def _extract_keywords(text: str) -> Counter:
    """Extract keywords from text, filtering stop words and short words."""
    words = _RE_WORD_SPLIT.split(text.lower())
    return Counter(
        w for w in words
        if len(w) > 2 and w not in _PREFILTER_STOP_WORDS
    )


def _extract_bigrams(text: str) -> set[str]:
    """Extract significant bigrams from text."""
    words = [
        w for w in _RE_WORD_SPLIT.split(text.lower())
        if len(w) > 2 and w not in _PREFILTER_STOP_WORDS
    ]
    return {f"{words[i]}_{words[i+1]}" for i in range(len(words) - 1)}


def _extract_profile_text(profile_text: str) -> str:
    """Extract all textual information from profile (handles both structured JSON and plain text)."""
    if not profile_text or not profile_text.strip():
        return ""
    
    # Try to parse as JSON
    try:
        data = json.loads(profile_text)
        if isinstance(data, dict):
            # Extract text from all relevant fields
            texts = []
            if data.get("org_name"):
                texts.append(data["org_name"])
            if data.get("org_type"):
                texts.append(data["org_type"])
            if data.get("competencies"):
                texts.append(data["competencies"])
            if data.get("past_experiences"):
                texts.append(data["past_experiences"])
            if data.get("technical_expertise"):
                texts.append(data["technical_expertise"])
            if data.get("partnerships"):
                texts.append(data["partnerships"])
            return " ".join(texts)
    except (json.JSONDecodeError, TypeError):
        pass
    
    # Fallback: return plain text as-is
    return profile_text


def prefilter_calls(
    active_df: pd.DataFrame,
    profile_text: str,
    top_k: int = CFG.PREFILTER_TOP_K,
) -> pd.DataFrame:
    """Pre-filter calls using TF-IDF based keyword matching before LLM screening."""
    # Extract textual content from profile (handles both formats)
    extracted_profile = _extract_profile_text(profile_text)
    profile_kw = _extract_keywords(extracted_profile)
    profile_bigrams = _extract_bigrams(extracted_profile)
    profile_vocab = set(profile_kw.keys())

    if not profile_vocab:
        return active_df.head(top_k)

    doc_freq: Counter = Counter()
    call_texts: list[str] = []
    for _, row in active_df.iterrows():
        text = f"{row.get('title', '')} {row.get('call_description', '') or row.get('summary', '')}"
        call_texts.append(text)
        call_kw = set(_extract_keywords(text).keys())
        for w in call_kw:
            doc_freq[w] += 1

    n_docs = len(active_df)
    idf: dict[str, float] = {}
    for w in profile_vocab:
        df = doc_freq.get(w, 0)
        idf[w] = math.log((n_docs + 1) / (df + 1)) + 1.0

    scores: list[float] = []
    for idx, text in enumerate(call_texts):
        call_kw = _extract_keywords(text)
        call_bigrams = _extract_bigrams(text)

        overlap = profile_vocab & set(call_kw.keys())
        unigram_score = sum(
            min(profile_kw[w], call_kw[w]) * idf.get(w, 1.0)
            for w in overlap
        )

        bigram_overlap = len(profile_bigrams & call_bigrams)
        bigram_score = bigram_overlap * 3.0

        title = active_df.iloc[idx].get("title", "")
        title_kw = set(_extract_keywords(title).keys())
        title_overlap = len(profile_vocab & title_kw)
        title_score = title_overlap * 5.0

        scores.append(unigram_score + bigram_score + title_score)

    active_df = active_df.copy()
    active_df["_prefilter_score"] = scores
    active_df = active_df.sort_values("_prefilter_score", ascending=False).reset_index(drop=True)

    return active_df.head(top_k)
