#!/usr/bin/env python3
"""
Diagnostic script to test F&T Search API language filtering
Identifies if the issue is:
1. API query method (how we're querying)
2. API not returning English content (API limitation)
3. English content not present for specific topics (data availability)
"""

import json
import requests
import sys
import os
import time

# Add modular to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modular.phase1_config import CFG
from modular.phase1_api import fetch_call_by_topic_id, parse_topic_json

# Test topics from Cluster 6 (work programme)
CLUSTER_6_TESTS = [
    "HORIZON-CL5-2026-03-D3-19",
    "HORIZON-CL5-2026-03-D3-20",
    "HORIZON-CL5-2026-03-D3-21",
    "HORIZON-CL5-2026-03-D3-22",
    "HORIZON-CL5-2026-03-D3-29",
]

def test_api_direct(topic_id: str, include_language_filter: bool = True):
    """Test API call directly to see raw response"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    # Strategy 1: Exact match
    payload = {"pageNumber": 1, "pageSize": 3}
    if include_language_filter:
        payload["languages"] = ["en"]
    
    url = f'{CFG.FT_SEARCH_BASE}?apiKey={CFG.API_KEY}&text="{topic_id}"'
    
    try:
        r = session.post(url, json=payload, timeout=15)
        
        if r.status_code == 200:
            data = r.json()
            results = data.get("results", [])
            
            return {
                "status": "OK",
                "count": len(results),
                "results": results,
                "with_filter": include_language_filter,
            }
        else:
            return {
                "status": "ERROR",
                "code": r.status_code,
                "message": r.text[:200],
                "with_filter": include_language_filter,
            }
    except Exception as e:
        return {
            "status": "EXCEPTION",
            "error": str(e),
            "with_filter": include_language_filter,
        }


def analyze_result(result: dict) -> dict:
    """Analyze a single result for language characteristics"""
    analysis = {
        "has_title": bool(result.get("title")),
        "has_description": bool(result.get("description") or result.get("metadata", {}).get("description")),
        "title_preview": None,
        "description_preview": None,
        "non_ascii_chars": 0,
        "detected_language": "unknown",
        "appears_english": False,
    }
    
    # Get title
    title = result.get("title")
    if isinstance(title, list):
        title = title[0] if title else None
    analysis["title_preview"] = str(title)[:80] if title else None
    
    # Get description
    desc = result.get("description") or result.get("metadata", {}).get("description")
    if isinstance(desc, list):
        desc = desc[0] if desc else None
    analysis["description_preview"] = str(desc)[:80] if desc else None
    
    # Check character encoding
    combined_text = str(analysis["title_preview"] or "") + " " + str(analysis["description_preview"] or "")
    non_ascii = [c for c in combined_text if ord(c) > 127]
    analysis["non_ascii_chars"] = len(non_ascii)
    
    # Simple English detection
    if non_ascii:
        # Has non-ASCII chars - likely not English
        analysis["appears_english"] = False
        
        # Try to detect language from non-ASCII chars
        if any(ord(c) in range(0x0400, 0x04FF) for c in non_ascii):
            analysis["detected_language"] = "Cyrillic (Bulgarian?)"
        elif any(ord(c) in range(0x0100, 0x017F) for c in non_ascii):
            analysis["detected_language"] = "Latin Extended (German/French?)"
        else:
            analysis["detected_language"] = "Other"
    else:
        analysis["appears_english"] = True
        analysis["detected_language"] = "Likely English (ASCII-only)"
    
    return analysis


def print_section(title: str):
    """Print a formatted section header"""
    print(f"\n{'═' * 80}")
    print(f"  {title}")
    print(f"{'═' * 80}")


def print_subsection(title: str):
    """Print a formatted subsection header"""
    print(f"\n{title}")
    print(f"{'-' * len(title)}")


def main():
    print("\n" + "=" * 80)
    print(" F&T SEARCH API - DIAGNOSTIC TEST")
    print("=" * 80)
    print(f"\nConfiguration:")
    print(f"  Base URL: {CFG.FT_SEARCH_BASE}")
    print(f"  API Key: {CFG.API_KEY}")
    print(f"  Test Topics: {len(CLUSTER_6_TESTS)}")
    
    print_section("PART 1: API COMPARISON - WITH vs WITHOUT Language Filter")
    
    # Pick one test topic for comparison
    test_topic = CLUSTER_6_TESTS[0]
    print(f"\nTesting topic: {test_topic}")
    
    print_subsection("Test 1A: WITH language filter (languages: ['en'])")
    result_with = test_api_direct(test_topic, include_language_filter=True)
    
    if result_with["status"] == "OK":
        print(f"✓ API returned {result_with['count']} results")
        if result_with['count'] > 0:
            first = result_with['results'][0]
            analysis = analyze_result(first)
            print(f"  Title: {analysis['title_preview']}")
            print(f"  Description: {analysis['description_preview']}")
            print(f"  Non-ASCII chars: {analysis['non_ascii_chars']}")
            print(f"  Appears English: {analysis['appears_english']}")
            print(f"  Detected language: {analysis['detected_language']}")
    else:
        print(f"✗ Error: {result_with.get('message', result_with.get('error'))}")
    
    # Wait before next request
    time.sleep(2)
    
    print_subsection("Test 1B: WITHOUT language filter (no languages parameter)")
    result_without = test_api_direct(test_topic, include_language_filter=False)
    
    if result_without["status"] == "OK":
        print(f"✓ API returned {result_without['count']} results")
        if result_without['count'] > 0:
            first = result_without['results'][0]
            analysis = analyze_result(first)
            print(f"  Title: {analysis['title_preview']}")
            print(f"  Description: {analysis['description_preview']}")
            print(f"  Non-ASCII chars: {analysis['non_ascii_chars']}")
            print(f"  Appears English: {analysis['appears_english']}")
            print(f"  Detected language: {analysis['detected_language']}")
    else:
        print(f"✗ Error: {result_without.get('message', result_without.get('error'))}")
    
    print_subsection("Comparison Results")
    if result_with["status"] == "OK" and result_without["status"] == "OK":
        with_count = result_with['count']
        without_count = result_without['count']
        print(f"Results WITH filter:    {with_count}")
        print(f"Results WITHOUT filter: {without_count}")
        if with_count == without_count:
            print(f"→ Filter has NO EFFECT (API ignoring the parameter)")
        elif with_count < without_count:
            print(f"→ Filter IS WORKING (returned fewer results with filter)")
    
    # Part 2: Test all Cluster 6 topics with both methods
    print_section("PART 2: Using Python fetch_call_by_topic_id() Function")
    
    print(f"\nTesting {len(CLUSTER_6_TESTS)} Cluster 6 topics with the app's fetch function...")
    print(f"(This uses language filter in both Strategy 1 and Strategy 2)\n")
    
    for i, topic in enumerate(CLUSTER_6_TESTS, 1):
        try:
            print(f"[{i}/{len(CLUSTER_6_TESTS)}] {topic}...", end=" ", flush=True)
            
            result = fetch_call_by_topic_id(topic)
            
            if result:
                parsed = parse_topic_json(topic, result)
                
                # Check for non-English content
                combined = (parsed.get("title", "") + " " + parsed.get("call_description", "")).encode('utf-8')
                non_ascii_count = sum(1 for c in combined if c > 127)
                
                if non_ascii_count > 0:
                    print(f"✗ Contains {non_ascii_count} non-ASCII bytes (likely non-English)")
                else:
                    print(f"✓ ASCII-only (likely English)")
            else:
                print(f"✗ Not found")
            
            time.sleep(CFG.API_FETCH_DELAY)  # Respect API rate limit
        except Exception as e:
            print(f"✗ Error: {str(e)[:50]}")
    
    print_section("PART 3: Diagnostic Conclusions")
    
    print("""
Based on the tests above, here's how to identify the root cause:

1️⃣  IF "WITH filter" returns SAME RESULTS as "WITHOUT filter":
    → The API is IGNORING the language parameter
    → Contact EC portal support to verify language filtering works
    → Problem is API-side, not your code

2️⃣  IF "WITH filter" returns FEWER or DIFFERENT results:
    → The API IS respecting the language filter
    → If results are still non-English:
      a) English version doesn't exist for that topic (API doesn't have it)
      b) The API is returning non-English despite filter (API bug)

3️⃣  IF fetch_call_by_topic_id() returns non-English content:
    → Either API issue (#1 or #2b above) OR
    → The specific topic's English version is not in the API

4️⃣  IF no results are found at all:
    → Topic ID might not exist in the API
    → Try searching via F&T portal web UI manually
    """)
    
    print_section("NEXT STEPS")
    
    print("""
If non-English content is still appearing:

Option A - Check if English version exists:
  1. Go to https://ec.europa.eu/info/funding-tenders/opportunities/portal/
  2. Search for the topic ID manually
  3. Check if English version is available there
  4. If NO English version in portal → API won't have it either

Option B - Reset your database and re-fetch:
  1. DELETE FROM horizon_calls WHERE cluster = '6';
  2. Re-upload the work programme
  3. Check if non-English content still appears
  4. If YES → likely API returning non-English despite filter
  5. If NO → you had cached old data

Option C - Contact EC Support:
  - Topic ID: [the one with issue]
  - Error: Non-English content despite language filter
  - Evidence: Output from this diagnostic script
  - API: F&T Search API (https://api.tech.ec.europa.eu/search-api/prod)
    """)
    
    print(f"\n{'═' * 80}")
    print(" Test Complete")
    print(f"{'═' * 80}\n")


if __name__ == "__main__":
    main()
