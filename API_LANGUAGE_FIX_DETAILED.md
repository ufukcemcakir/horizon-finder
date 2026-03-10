# API Language Filtering - Before & After Comparison

## Overview
This document compares the old implementation (without language filtering) vs the current implementation (with language filtering) to clarify the fix that was applied.

---

## File Structure

```
horizon/
├── modular/
│   └── phase1_api.py          ← CURRENT IMPLEMENTATION (with language filter)
├── phase1_api.py               ← OLD IMPLEMENTATION (without language filter - obsolete)
└── app.py                       ← Uses modular/phase1_api.py ✓
```

---

## Side-by-Side Comparison

### OLD IMPLEMENTATION (phase1_api.py) ❌

**Strategy 1: Exact Match**
```python
def fetch_call_by_topic_id(topic_id: str) -> Optional[dict]:
    session = _get_api_session()
    try:
        r = session.post(
            f'{CFG.FT_SEARCH_BASE}?apiKey={CFG.API_KEY}&text="{topic_id}"',
            json={"pageNumber": 1, "pageSize": 1},  # ← NO language filter
            timeout=20,
        )
        if r.status_code == 200:
            results = r.json().get("results") or []
            if results:
                return results[0]
    except Exception:
        pass
```

**Strategy 2: Fallback Search**
```python
    try:
        r = session.post(
            f"{CFG.FT_SEARCH_BASE}?apiKey={CFG.API_KEY}",
            json={"pageNumber": 1, "pageSize": 5, "text": topic_id},  # ← NO language filter
            timeout=20,
        )
        if r.status_code == 200:
            return _pick_best_result(r.json().get("results") or [], topic_id)
    except Exception:
        pass
```

**Problem:** Without the `languages: ["en"]` filter, the API returns results in ALL languages, including Bulgarian, German, French, etc.

---

### NEW IMPLEMENTATION (modular/phase1_api.py) ✅

**Strategy 1: Exact Match**
```python
def fetch_call_by_topic_id(topic_id: str) -> Optional[dict]:
    """Fetch a single call from the F&T Search API."""
    session = _get_api_session()
    
    # Strategy 1: exact-match quoted search with language filter
    try:
        r = session.post(
            f'{CFG.FT_SEARCH_BASE}?apiKey={CFG.API_KEY}&text="{topic_id}"',
            json={"pageNumber": 1, "pageSize": 1, "languages": ["en"]},  # ← ADDED language filter
            timeout=20,
        )
        if r.status_code == 200:
            results = r.json().get("results") or []
            if results:
                return results[0]
    except Exception:
        pass
```

**Strategy 2: Fallback Search**
```python
    # Strategy 2: broader text search + best-pick with language filter
    try:
        r = session.post(
            f"{CFG.FT_SEARCH_BASE}?apiKey={CFG.API_KEY}",
            json={"pageNumber": 1, "pageSize": 5, "text": topic_id, "languages": ["en"]},  # ← ADDED language filter
            timeout=20,
        )
        if r.status_code == 200:
            return _pick_best_result(r.json().get("results") or [], topic_id)
    except Exception:
        pass
```

**Solution:** Added `"languages": ["en"]` to both API calls. The API now only returns English-language documents.

---

## What Changed?

### Change 1: Strategy 1 Request Body
```diff
- json={"pageNumber": 1, "pageSize": 1}
+ json={"pageNumber": 1, "pageSize": 1, "languages": ["en"]}
```

### Change 2: Strategy 2 Request Body
```diff
- json={"pageNumber": 1, "pageSize": 5, "text": topic_id}
+ json={"pageNumber": 1, "pageSize": 5, "text": topic_id, "languages": ["en"]}
```

### Change 3: Documentation
- Added docstring to `fetch_call_by_topic_id()`
- Added strategy labels and comments
- Made code more maintainable

---

## API Behavior Comparison

### Without Language Filter (OLD)
```
Request: text="HORIZON-2024-CL4-12345"
         languages: NOT SPECIFIED

Response Options:
✓ English result (wanted)
✓ Bulgarian result (unwanted)
✓ German result (unwanted)
✓ French result (unwanted)

Actual: Mixed language results returned
```

### With Language Filter (NEW)
```
Request: text="HORIZON-2024-CL4-12345"
         languages: ["en"]

Response Options:
✓ English result (wanted)
✗ Bulgarian result (filtered out by API)
✗ German result (filtered out by API)
✗ French result (filtered out by API)

Actual: Only English results returned
```

---

## Impact on Application

### Before Fix
- ❌ Non-English content could be saved to database
- ❌ Users might see Bulgarian/German/French titles
- ❌ Descriptions in non-English languages
- ❌ Inconsistent user experience

### After Fix
- ✅ Only English content in database
- ✅ Consistent English titles for all topics
- ✅ All descriptions in English
- ✅ Professional, consistent UX

---

## Testing the Language Filter

### How to Verify
1. Make API request WITHOUT `languages: ["en"]`
   - Result: May include non-English documents
   
2. Make API request WITH `languages: ["en"]`
   - Result: Only English documents

### Example Test Code
```python
import requests

session = requests.Session()

# Request WITHOUT language filter
r1 = session.post(
    "https://api.tech.ec.europa.eu/search-api/prod/rest/search?apiKey=SEDIA",
    json={"pageNumber": 1, "pageSize": 5, "text": "HORIZON-2024"}
)
print("WITHOUT filter:", r1.json()["results"][0]["title"])

# Request WITH language filter
r2 = session.post(
    "https://api.tech.ec.europa.eu/search-api/prod/rest/search?apiKey=SEDIA",
    json={"pageNumber": 1, "pageSize": 5, "text": "HORIZON-2024", "languages": ["en"]}
)
print("WITH filter:", r2.json()["results"][0]["title"])
```

---

## Why Both Strategies Keep the Filter

The two-strategy approach is important:

1. **Strategy 1** = Fast, precise query
   - Only needed if exact match exists
   - Still needs language filter (why return 10 languages to pick 1?)

2. **Strategy 2** = Fallback for edge cases
   - Used if Strategy 1 finds nothing
   - MUST have language filter (prevents returning non-English results)

**Both strategies must filter** to ensure the application always works in English.

---

## Database Migration Note

### Important
- Old data in database might contain non-English content
- New fetches will only add English content
- To clean up old database: Consider re-fetching existing topics

---

## Related Documentation
- [API_LANGUAGE_FIX_SUMMARY.md](API_LANGUAGE_FIX_SUMMARY.md) - Implementation summary
- `modular/phase1_api.py` - Current implementation
- `app.py` - Flask integration
- `test_ft_api.py` - Test script

---

## Conclusion

The fix is **simple but critical**:
- Added `"languages": ["en"]` parameter to both API calls
- Ensures only English-language documents are returned
- Improves data quality and user experience
- Currently deployed and active in `modular/phase1_api.py`

