# check_api.py
# ----------- Standalone diagnostic script — run from terminal:
# python check_api.py
#
# Uses the EXACT fetch logic from the working notebook to pull one call
# and print every field the API returns, so you can see whether
# scope / expectedOutcomes come back at all.

import requests
import json

API_KEY = "SEDIA"
BASE = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"

# ── Change this to any topic ID you know is in your DB ──────────────────────
TOPIC_ID = "HORIZON-CL5-2024-D3-01-01"
# ────────────────────────────────────────────────────────────────────────────


def fetch_topic_details(call_identifier):
    """Copied verbatim from the working notebook."""
    url = f'{BASE}?apiKey={API_KEY}&text="{call_identifier}"'
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    body = {"pageNumber": 1, "pageSize": 1}

    try:
        r = requests.post(url, headers=headers, json=body, timeout=20)
        if r.status_code != 200:
            print(f"[{r.status_code}] Error fetching {call_identifier}")
            return None

        data = r.json()
        if "results" not in data or len(data["results"]) == 0:
            print(f"No results for {call_identifier}")
            return None

        return data["results"][0]

    except Exception as e:
        print("Exception:", e)
        return None


def _pick_first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _safe_get(dct, key, default=None):
    if not isinstance(dct, dict):
        return default
    return dct.get(key, default)


def inspect(result):
    print("\n" + "=" * 60)
    print("TOP-LEVEL KEYS:")
    print(list(result.keys()))

    print("\n--- KEY FIELDS (top-level) ---")
    for field in [
        "identifier", "callIdentifier", "reference", "title", "summary",
        "scope", "expectedOutcomes", "deadlineDate", "startDate", "url"
    ]:
        val = _pick_first(_safe_get(result, field))
        is_empty = not val or str(val).strip() in ("", "None")
        flag = " EMPTY" if is_empty else " "
        print(f" {flag} {field}: {str(val)[:120] if val else None}")

    md = _safe_get(result, "metadata", {})

    print("\n--- METADATA KEYS ---")
    print(list(md.keys()) if md else "(no metadata block)")

    print("\n--- KEY FIELDS (metadata) ---")
    for field in [
        "status", "typesOfAction", "programmePeriod", "frameworkProgramme",
        "scope", "expectedOutcomes", "description", "topicDescription",
        "topicExpectedOutcomes", "deadlineDate", "startDate", "title"
    ]:
        val = _pick_first(_safe_get(md, field))
        is_empty = not val or str(val).strip() in ("", "None")
        flag = " EMPTY" if is_empty else " "
        print(f" {flag} metadata.{field}: {str(val)[:120] if val else None}")

    print("\n--- FULL RAW JSON ---")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    print(f"Fetching: {TOPIC_ID}")
    result = fetch_topic_details(TOPIC_ID)
    if result:
        inspect(result)
    else:
        print("Nothing returned. Check your TOPIC_ID or network connection.")