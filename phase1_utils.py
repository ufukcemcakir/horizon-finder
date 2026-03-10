import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

from phase1_config import CFG, STATUS_MAP, ALLOWED_ACTION_TYPES


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


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


_STOP_WORDS_NETWORK = frozenset({
    "with", "that", "this", "from", "have", "been", "will", "their", "they",
    "also", "such", "more", "than", "which", "would", "into", "about",
    "other", "these", "some", "over",
})
_RE_ALPHA_ONLY = re.compile(r"[^a-zA-Z0-9\s-]")


def suggest_keywords_from_profile(profile_text: str, top_k: int = 12) -> list[str]:
    tokens = [
        t for t in _RE_ALPHA_ONLY.sub(" ", profile_text.lower()).split()
        if len(t) > 3 and t not in _STOP_WORDS_NETWORK
    ]
    return [w for w, _ in Counter(tokens).most_common(top_k)]
