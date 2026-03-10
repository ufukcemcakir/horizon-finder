import json
from typing import Any, Optional

import requests

from .phase1_config import CFG
from .phase1_utils import strip_html


def _pick_first(v: Any) -> Any:
    return v[0] if isinstance(v, (list, tuple)) and v else v


def _sg(dct: Any, key: str, default: Any = None) -> Any:
    return dct.get(key, default) if isinstance(dct, dict) else default


def _pf(dct: Any, key: str) -> Any:
    return _pick_first(_sg(dct, key))


def _is_bulgarian_content(text: str) -> bool:
    """Detect if text contains Bulgarian (Cyrillic) characters"""
    if not text:
        return False
    # Check for Cyrillic Unicode range (0x0400-0x04FF)
    cyrillic_count = sum(1 for c in text if 0x0400 <= ord(c) <= 0x04FF)
    if len(text) > 0:
        cyrillic_ratio = cyrillic_count / len(text)
        return cyrillic_ratio > 0.05  # More than 5% Cyrillic = Bulgarian
    return False


def _filter_english_only(api_result: dict) -> Optional[dict]:
    """
    Filter out Bulgarian content. Returns None if result is Bulgarian.
    Checks: title, summary, descriptionByte (the fields that contain Bulgarian).
    """
    if not api_result:
        return None
    
    md = _sg(api_result, "metadata", {})
    
    # Check key fields for Bulgarian content
    critical_fields = [
        api_result.get("title"),
        api_result.get("summary") or _sg(md, "summary"),
        _sg(md, "descriptionByte") or _sg(md, "description"),
    ]
    
    for value in critical_fields:
        # Handle list values
        if isinstance(value, list):
            value = " ".join(str(v) for v in value if v)
        
        value = str(value or "")
        
        if value and _is_bulgarian_content(value):
            # This result is Bulgarian - filter it out
            return None
    
    return api_result


def _get_description_byte(result_item: dict) -> str:
    md = _sg(result_item, "metadata") or {}
    raw = _sg(md, "descriptionByte") or ""
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    raw = str(raw).strip()
    return strip_html(raw) if raw else ""


def _get_api_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Accept": "application/json"})
    return s


def fetch_call_by_topic_id(topic_id: str) -> Optional[dict]:
    """Fetch a single call from the F&T Search API, filtering out Bulgarian content."""
    session = _get_api_session()
    
    # Strategy 1: exact-match quoted search
    try:
        r = session.post(
            f'{CFG.FT_SEARCH_BASE}?apiKey={CFG.API_KEY}&text="{topic_id}"',
            json={"pageNumber": 1, "pageSize": 1, "languages": ["en"]}, timeout=20,
        )
        if r.status_code == 200:
            results = r.json().get("results") or []
            for result in results:
                filtered = _filter_english_only(result)
                if filtered:
                    return filtered
    except Exception:
        pass
    
    # Strategy 2: broader text search + best-pick
    try:
        r = session.post(
            f"{CFG.FT_SEARCH_BASE}?apiKey={CFG.API_KEY}",
            json={"pageNumber": 1, "pageSize": 5, "text": topic_id, "languages": ["en"]}, timeout=20,
        )
        if r.status_code == 200:
            results = r.json().get("results") or []
            # Filter out Bulgarian results before picking best
            english_results = [r for r in results if _filter_english_only(r)]
            return _pick_best_result(english_results, topic_id)
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
