#!/usr/bin/env python3
"""
Enhanced F&T API diagnostic - Testing alternative language filter formats
Since languages: ["en"] doesn't work, let's try other approaches
"""

import json
import requests
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modular.phase1_config import CFG
from modular.phase1_api import fetch_call_by_topic_id, parse_topic_json

# Real problematic topics that return Bulgarian
CLUSTER_5_TESTS = [
    "HORIZON-CL5-2026-03-D3-19",
    "HORIZON-CL5-2026-03-D3-20",
    "HORIZON-CL5-2026-03-D3-21",
]

def test_language_parameter_variations(topic_id: str):
    """Test different ways the API might accept language filtering"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    base_url = f'{CFG.FT_SEARCH_BASE}?apiKey={CFG.API_KEY}&text="{topic_id}"'
    
    test_cases = [
        {
            "name": 'languages: ["en"]',
            "payload": {"pageNumber": 1, "pageSize": 1, "languages": ["en"]},
            "description": "Current approach (not working)"
        },
        {
            "name": 'locale: "en"',
            "payload": {"pageNumber": 1, "pageSize": 1, "locale": "en"},
            "description": "Try locale instead of languages"
        },
        {
            "name": 'language: "en"',
            "payload": {"pageNumber": 1, "pageSize": 1, "language": "en"},
            "description": "Singular language parameter"
        },
        {
            "name": 'selectedLanguages: ["en"]',
            "payload": {"pageNumber": 1, "pageSize": 1, "selectedLanguages": ["en"]},
            "description": "Alternative name"
        },
        {
            "name": 'filterLanguage: "en"',
            "payload": {"pageNumber": 1, "pageSize": 1, "filterLanguage": "en"},
            "description": "Different naming"
        },
        {
            "name": 'lang: "en"',
            "payload": {"pageNumber": 1, "pageSize": 1, "lang": "en"},
            "description": "Abbreviation"
        },
        {
            "name": 'in_language: "en"',
            "payload": {"pageNumber": 1, "pageSize": 1, "in_language": "en"},
            "description": "Different format"
        },
        {
            "name": 'languages: "en" (string not array)',
            "payload": {"pageNumber": 1, "pageSize": 1, "languages": "en"},
            "description": "String instead of array"
        },
        {
            "name": 'No filter (baseline)',
            "payload": {"pageNumber": 1, "pageSize": 1},
            "description": "Without any language parameter"
        },
    ]
    
    print(f"\nTesting alternative language filter formats for: {topic_id}")
    print("=" * 80)
    
    results_summary = []
    
    for i, test_case in enumerate(test_cases, 1):
        try:
            r = requests.post(base_url, json=test_case["payload"], timeout=10)
            
            if r.status_code == 200:
                data = r.json()
                result_count = len(data.get("results", []))
                
                # Check first result for language
                first_result = data.get("results", [{}])[0] if data.get("results") else {}
                title = first_result.get("title")
                if isinstance(title, list):
                    title = title[0] if title else None
                
                # Detect non-ASCII
                title_str = str(title or "")
                non_ascii = sum(1 for c in title_str if ord(c) > 127)
                
                status = "🔴" if non_ascii > 0 else "🟢"
                lang_hint = "(Bulgarian/Cyrillic)" if non_ascii > 0 else "(English/ASCII)"
                
                results_summary.append({
                    "param": test_case["name"],
                    "count": result_count,
                    "non_ascii": non_ascii,
                    "status": status,
                    "title_sample": title_str[:60] if title else "N/A"
                })
                
                print(f"[{i:d}] {test_case['name']:<40} {status}")
                print(f"      Results: {result_count:<3} | Non-ASCII: {non_ascii:<4} | {lang_hint}")
                print(f"      Description: {test_case['description']}")
                if non_ascii == 0 and title:
                    print(f"      Title: {title_str[:60]}")
                print()
            else:
                print(f"[{i}] {test_case['name']:<40} ❌ HTTP {r.status_code}")
                print()
        except Exception as e:
            print(f"[{i}] {test_case['name']:<40} ❌ Error: {str(e)[:50]}")
            print()
        
        time.sleep(0.5)  # Rate limiting
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY:")
    print("=" * 80)
    
    english_results = [r for r in results_summary if r["non_ascii"] == 0]
    bulgarian_results = [r for r in results_summary if r["non_ascii"] > 0]
    
    if english_results:
        print(f"\n✅ Parameters that returned ENGLISH content:")
        for r in english_results:
            print(f"   • {r['param']:<40} ({r['count']} results)")
    
    if bulgarian_results:
        print(f"\n🔴 Parameters that returned BULGARIAN content:")
        for r in bulgarian_results:
            print(f"   • {r['param']:<40} ({r['count']} results with {r['non_ascii']} non-ASCII chars)")
    
    if not english_results and not bulgarian_results:
        print("\n⚠️  No conclusive results")


def detect_bulgarian_in_title(title_str: str) -> bool:
    """Detect if a title contains Bulgarian/Cyrillic characters"""
    return any(0x0400 <= ord(c) <= 0x04FF for c in title_str)


def main():
    print("\n" + "=" * 80)
    print(" F&T API - Language Filter Parameter Testing")
    print("=" * 80)
    print(f"\nObjective: Find which parameter format filters for English content")
    print(f"API: {CFG.FT_SEARCH_BASE}")
    
    # Test each problematic topic
    for topic in CLUSTER_5_TESTS[:1]:  # Start with first one to save time
        test_language_parameter_variations(topic)
        time.sleep(2)
    
    print("\n" + "=" * 80)
    print(" RECOMMENDATIONS")
    print("=" * 80)
    
    print("""
Based on the test results above, here's what to do:

1️⃣  If you found a parameter that returns ENGLISH content:
    → Update modular/phase1_api.py to use that parameter instead
    → Change lines 44 and 56 to use the working parameter

2️⃣  If NO parameter returns English content:
    → The API may not support English filtering
    → The API might only have Bulgarian versions of these topics
    → Consider:
       a) Post-processing: Translate Bulgarian → English
       b) Flag: Mark Bulgarian topics for manual review
       c) Skip: Don't include topics without English versions

3️⃣  Check the F&T API documentation:
    → https://api.tech.ec.europa.eu/search-api/prod/
    → Look for "language" or "locale" parameters
    → Check if different parameter name/format is documented

4️⃣  Contact EC Support:
    → Topic IDs returning Bulgarian despite language filters
    → Ask if these topics have English versions in the API at all
    → Request documentation on language filtering parameter
    """)
    
    print(f"\n{'=' * 80}\n")


if __name__ == "__main__":
    main()
