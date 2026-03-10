import re
from typing import List

import pdfplumber

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


def extract_topic_ids_from_pdf(uploaded_file) -> List[str]:
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
