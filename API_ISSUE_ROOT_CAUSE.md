# F&T API Language Filter Issue - Root Cause & Solutions

## The Problem (CONFIRMED)

The F&T Search API **is NOT respecting the `languages: ["en"]` parameter**.

### Evidence:
```
WITH languages: ["en"]     → 10 results (Bulgarian content)
WITHOUT language filter    → 10 results (Bulgarian content)
Difference: 0 results
```

The language filter has **NO EFFECT** - the API returns identical results regardless of the parameter.

---

## Root Cause Options

1. **API Bug** - The language parameter is broken on EC's side
2. **Wrong Parameter Name** - The API uses a different parameter name
3. **Wrong Parameter Format** - The API expects data in a different format
4. **No English Version** - These specific topics only have Bulgarian content in the API

---

## Three-Step Solution Plan

### STEP 1: Test Alternative Parameter Names
```bash
python test_language_filters.py
```

This tests 8 different parameter variations:
- `languages: ["en"]` (current - not working)
- `locale: "en"`
- `language: "en"`
- `selectedLanguages: ["en"]`
- Plus 4 more variations

**Expected output:**
```
[1] languages: ["en"]                        🔴 (Bulgarian/Cyrillic)
[2] locale: "en"                             🟢 (English/ASCII)       ← IF THIS WORKS
[3] language: "en"                           🔴 (Bulgarian/Cyrillic)
...
```

If you find a parameter that shows 🟢 (English), that's the fix!

---

### STEP 2: If a Parameter Works
Update `modular/phase1_api.py` to use the working parameter.

**Example:** If `locale: "en"` works, change lines 44 and 56:

```python
# Line 44 - Change FROM:
json={"pageNumber": 1, "pageSize": 1, "languages": ["en"]}

# Change TO:
json={"pageNumber": 1, "pageSize": 1, "locale": "en"}

# And similarly on line 56
```

---

### STEP 3: If NO Parameter Works
**Apply Client-Side Filtering** to filter out Bulgarian content:

```bash
python filter_bulgarian.py
```

This will:
1. Fetch results from API (even if Bulgarian)
2. Detect Bulgarian content using Cyrillic detection
3. Filter out Bulgarian results client-side
4. Return only English content

Then integrate it into `modular/phase1_api.py`:

```python
from filter_bulgarian import filter_english_only

def fetch_call_by_topic_id(topic_id: str) -> Optional[dict]:
    """Fetch a single call from the F&T Search API."""
    session = _get_api_session()
    
    try:
        r = session.post(
            f'{CFG.FT_SEARCH_BASE}?apiKey={CFG.API_KEY}&text="{topic_id}"',
            json={"pageNumber": 1, "pageSize": 1},  # Remove language filter
            timeout=20,
        )
        if r.status_code == 200:
            results = r.json().get("results") or []
            if results:
                first = results[0]
                # Filter out Bulgarian content
                return filter_english_only(first)
    except Exception:
        pass
    
    # Strategy 2 (similar changes)
    ...
```

---

## Action Items

### Immediate (Next 5 minutes)
```bash
# 1. Try alternative language filter parameters
python test_language_filters.py

# 2. Run this and SHARE THE OUTPUT with me
```

### If a Parameter Works
```bash
# 1. Tell me which parameter worked
# 2. I'll update phase1_api.py with the working parameter
# 3. Re-upload work programme to verify
```

### If NO Parameter Works
```bash
# 1. We'll implement client-side Bulgarian filtering
# 2. This filters out Bulgarian content after fetching
# 3. Your users will only see English results (or nothing if English not available)
```

---

## What Each Solution Means

### Option 1: Alternative Parameter Works
- ✅ **Best outcome** - API supports filtering, just different parameter name
- Time to fix: 5 minutes
- Example: Change `languages: ["en"]` to `locale: "en"`

### Option 2: Client-Side Filtering
- ⚠️  **Workaround** - We filter Bulgarian content after fetching
- Time to fix: 15 minutes
- Pros: Guaranteed to work, no API dependency
- Cons: All Bulgarian topics will be skipped/hidden

### Option 3: API Limitation
- ❌ **Worst case** - API genuinely doesn't support language filtering
- Next step: Contact EC support or manually manage topics
- Long term: Watch for API updates

---

## Expected Outcomes by Parameter

If `test_language_filters.py` shows this, it means:

```
✅ Parameters that returned ENGLISH content:
   • locale: "en"                           (50 results)

→ Use `locale: "en"` instead of `languages: ["en"]`
```

OR

```
🔴 Parameters that returned BULGARIAN content:
   • languages: ["en"]                      (10 results with 5240 non-ASCII)
   • locale: "en"                           (10 results with 5240 non-ASCII)
   • language: "en"                         (10 results with 5240 non-ASCII)
   [... all others similar ...]

→ No parameter works. Use client-side filtering instead.
```

---

## Quick Reference

| Finding | What It Means | Next Step |
|---------|---------------|-----------|
| 1 parameter shows English, others Bulgarian | API works but wrong param name | Update phase1_api.py |
| All parameters show Bulgarian | API doesn't filter | Implement client-side filter |
| Inconsistent results across parameters | API behavior varies | Document which param is most reliable |
| API errors/timeouts | API issue | Contact EC support |

---

## To Run the Enhanced Diagnostic

```bash
# Make sure you're in the project directory
cd c:\Users\ufuk.cakir\horizon

# Activate venv if not already
venv\Scripts\activate

# Run the diagnostic
python test_language_filters.py

# SAVE THE OUTPUT - send it to me for analysis
```

Expected runtime: ~2-3 minutes (it tests 9 variations with delays between requests)

---

## Once You Have Results

Please tell me:

1. **Which parameters returned ENGLISH content?** (🟢)
2. **Which parameters returned BULGARIAN content?** (🔴)
3. **Did any parameter show a difference in result count?**

Based on that, I'll either:
- Update `phase1_api.py` with the correct parameter, OR
- Implement client-side Bulgarian filtering

---

## Summary

You've found a **critical API issue**: the language filter doesn't work.

Next: Run `python test_language_filters.py` to find if another parameter works.

If no other parameter works: We'll implement client-side filtering to detect and remove Bulgarian content.

**This is solvable in < 30 minutes either way!**
