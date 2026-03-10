import json
import time
from typing import Any, Optional, List, Dict

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


# ═══════════════════════════════════════════════════════════════════════════════
# EU FUNDING & TENDERS SEARCH API - AUTOMATIC CALL FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

GRANTS_ONLY_TYPES = ["1", "2", "8"]  # Exclude tenders (type 0)
OPEN_STATUS_CODES = ["31094501"]

# Cluster keyword mappings
CLUSTER_KEYWORDS = {
    "Digital Industry and Space": [
        "digital", "ai", "artificial intelligence", "autonomous systems", "space", 
        "cybersecurity", "blockchain", "metaverse", "computing", "photonics"
    ],
    "Health": [
        "health", "medicine", "disease", "clinical", "biotechnology", "genomics",
        "pharmaceutical", "vaccine", "cancer", "mental health", "pandemic"
    ],
    "Climate and Biodiversity": [
        "climate", "environment", "biodiversity", "green", "sustainable", "carbon",
        "net-zero", "renewable", "ocean", "forest", "ecosystem"
    ],
    "Energy": [
        "energy", "renewable", "nuclear", "hydrogen", "grid", "battery", "solar",
        "wind", "fusion", "power"
    ],
    "Mobility": [
        "mobility", "transport", "autonomous", "vehicle", "aviation", "rail",
        "shipping", "logistics", "electric", "battery"
    ],
    "Food and Agriculture": [
        "food", "agriculture", "farming", "crop", "livestock", "nutrition",
        "agritech", "soil", "water"
    ],
}


def fetch_open_grant_calls(
    page_size: int = 50,
    language: Optional[str] = "en",
    sleep_s: float = 0.1,
    timeout_s: int = 60,
) -> List[Dict[str, Any]]:
    """
    Fetch ALL open GRANT calls from EU Funding & Tenders Search API.
    Excludes tenders and non-English content.
    """
    API_URL = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
    API_KEY = "SEDIA"
    
    query = {
        "bool": {
            "must": [
                {"terms": {"type": GRANTS_ONLY_TYPES}},
                {"terms": {"status": OPEN_STATUS_CODES}},
            ]
        }
    }

    languages = [language] if language else None
    sort = {"field": "sortStatus", "order": "ASC"}

    page = 1
    all_results: List[Dict[str, Any]] = []

    while True:
        params = {
            "apiKey": API_KEY,
            "text": "***",
            "pageSize": str(page_size),
            "pageNumber": str(page),
        }

        files = {
            "query": ("blob", json.dumps(query), "application/json"),
            "sort": ("blob", json.dumps(sort), "application/json"),
        }
        if languages is not None:
            files["languages"] = ("blob", json.dumps(languages), "application/json")

        try:
            resp = requests.post(API_URL, params=params, files=files, timeout=timeout_s)
            if resp.status_code != 200:
                raise RuntimeError(f"API error: {resp.status_code}")

            data = resp.json()
            results = data.get("results", [])

            # Filter out Bulgarian content
            english_results = [r for r in results if _filter_english_only(r)]
            all_results.extend(english_results)

            print(f"page={page} got={len(english_results)}/{len(results)} total={len(all_results)}")

            if len(results) < page_size:
                break

            page += 1
            time.sleep(sleep_s)
        except Exception as e:
            print(f"Error fetching page {page}: {e}")
            break

    return all_results


def assign_cluster(title: str, description: str) -> str:
    """
    Assign a call to a cluster based on keywords in title and description.
    Uses keyword matching against known clusters.
    """
    text = (title + " " + description).lower()
    
    # Score each cluster
    scores: Dict[str, int] = {}
    for cluster, keywords in CLUSTER_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores[cluster] = score
    
    # Return cluster with highest score
    if scores:
        return max(scores, key=scores.get)
    
    return "Other"


def parse_api_call(api_item: Dict[str, Any], topic_id: Optional[str] = None) -> Dict[str, str]:
    """
    Convert an API result item to the database call format.
    Extracts unique topic_id from API response.
    """
    if not api_item:
        return {}
    
    # Extract topic_id from API response FIRST
    md = _sg(api_item, "metadata", {})
    extracted_topic_id = (
        _pf(api_item, "identifier") or 
        _pf(api_item, "callIdentifier") or 
        _pf(md, "callIdentifier") or 
        _pf(md, "identifier") or 
        topic_id or 
        "API-CALL"
    )
    
    # Now parse with the extracted topic_id
    parsed = parse_topic_json(str(extracted_topic_id), api_item)
    
    # Normalize status to readable format
    status_raw = parsed.get("status", "")
    status = str(status_raw).strip().lower()
    
    # Try to extract from JSON if it's stringified
    try:
        if status.startswith('{'):
            status_obj = json.loads(status_raw)
            status = str(status_obj.get("id") or status_obj.get("label", "")).lower()
    except:
        pass
    
    # Map to readable status
    if "31094501" in status or "open" in status:
        parsed["status"] = "Open"
    elif "31094502" in status or "forthcoming" in status:
        parsed["status"] = "Forthcoming"
    elif "31094503" in status or "closed" in status:
        parsed["status"] = "Closed"
    else:
        parsed["status"] = "Open"  # Default to Open for API fetches
    
    # Add cluster assignment
    title = parsed.get("title", "")
    description = parsed.get("call_description", "")
    cluster = assign_cluster(title, description)
    parsed["cluster"] = cluster
    
    return parsed
