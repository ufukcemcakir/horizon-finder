#!/usr/bin/env python3
"""
Better diagnostic that checks ALL fields like parse_topic_json() does
Identifies which fields contain Bulgarian content
"""

import json
import requests
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modular.phase1_config import CFG
from modular.phase1_utils import strip_html

def is_bulgarian_or_cyrillic(text: str) -> bool:
    """Check if text contains Cyrillic (Bulgarian) characters"""
    if not text:
        return False
    cyrillic_count = sum(1 for c in text if 0x0400 <= ord(c) <= 0x04FF)
    if len(text) > 0:
        ratio = cyrillic_count / len(text)
        return ratio > 0.05  # More than 5% Cyrillic
    return False


def get_description_byte(result_item: dict) -> str:
    """Extract descriptionByte like phase1_api does"""
    md = result_item.get("metadata", {})
    raw = md.get("descriptionByte") or ""
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    raw = str(raw).strip()
    return strip_html(raw) if raw else ""


def check_all_fields(result_item: dict) -> dict:
    """Check all relevant fields for Bulgarian content"""
    md = result_item.get("metadata", {})
    
    fields_to_check = {
        "title": result_item.get("title") or md.get("title"),
        "description": result_item.get("description") or md.get("description"),
        "summary": result_item.get("summary") or md.get("summary"),
        "shortDescription": result_item.get("shortDescription") or md.get("shortDescription"),
        "scope": result_item.get("scope") or md.get("scope"),
        "expectedOutcomes": result_item.get("expectedOutcomes") or md.get("expectedOutcomes"),
        "descriptionByte": get_description_byte(result_item),
        "identifier": result_item.get("identifier") or result_item.get("callIdentifier"),
    }
    
    analysis = {}
    
    for field_name, value in fields_to_check.items():
        # Convert lists to strings
        if isinstance(value, list):
            value = " ".join(str(v) for v in value if v)
        
        value = str(value or "")
        
        if not value:
            analysis[field_name] = {
                "present": False,
                "is_bulgarian": False,
                "non_ascii_count": 0,
                "preview": "N/A"
            }
        else:
            non_ascii = sum(1 for c in value if ord(c) > 127)
            is_bulgarian = is_bulgarian_or_cyrillic(value)
            
            analysis[field_name] = {
                "present": True,
                "is_bulgarian": is_bulgarian,
                "non_ascii_count": non_ascii,
                "preview": value[:80]
            }
    
    return analysis


def test_topic_deeply(topic_id: str):
    """Test a single topic and check all fields"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    url = f'{CFG.FT_SEARCH_BASE}?apiKey={CFG.API_KEY}&text="{topic_id}"'
    payload = {"pageNumber": 1, "pageSize": 1, "languages": ["en"]}
    
    print(f"\n{'=' * 80}")
    print(f"Testing: {topic_id}")
    print(f"{'=' * 80}")
    
    try:
        r = requests.post(url, json=payload, timeout=10)
        
        if r.status_code != 200:
            print(f"❌ API Error: {r.status_code}")
            return
        
        data = r.json()
        results = data.get("results", [])
        
        if not results:
            print("❌ No results found")
            return
        
        print(f"✓ Found {len(results)} results\n")
        
        first_result = results[0]
        analysis = check_all_fields(first_result)
        
        print("Field-by-field analysis:")
        print("-" * 80)
        
        bulgarian_fields = []
        english_fields = []
        
        for field_name, info in analysis.items():
            if not info["present"]:
                status = "⚪"
                print(f"{status} {field_name:<25} (empty/not present)")
            elif info["is_bulgarian"]:
                status = "🔴"
                bulgarian_fields.append(field_name)
                print(f"{status} {field_name:<25} BULGARIAN ({info['non_ascii_count']} non-ASCII chars)")
                print(f"   Preview: {info['preview']}\n")
            else:
                status = "🟢"
                english_fields.append(field_name)
                print(f"{status} {field_name:<25} English/ASCII ({info['non_ascii_count']} non-ASCII chars)")
                if info['preview'] != "N/A":
                    print(f"   Preview: {info['preview']}\n")
        
        print("\n" + "=" * 80)
        print("SUMMARY:")
        print("=" * 80)
        
        if bulgarian_fields:
            print(f"🔴 BULGARIAN content found in:")
            for field in bulgarian_fields:
                print(f"   • {field}")
            print()
        
        if english_fields:
            print(f"🟢 ENGLISH content found in:")
            for field in english_fields:
                print(f"   • {field}")
            print()
        
        # Check raw_json too
        print("\nChecking raw_json field:")
        print("-" * 80)
        raw_json = first_result.get("raw_json", "{}")
        if raw_json:
            non_ascii_in_raw = sum(1 for c in str(raw_json) if ord(c) > 127)
            if is_bulgarian_or_cyrillic(str(raw_json)):
                print(f"🔴 raw_json contains BULGARIAN ({non_ascii_in_raw} non-ASCII chars)")
            else:
                print(f"🟢 raw_json is English/ASCII ({non_ascii_in_raw} non-ASCII chars)")
        
    except Exception as e:
        print(f"❌ Error: {e}")


def main():
    print("\n" + "=" * 80)
    print(" F&T API - DEEP FIELD ANALYSIS")
    print("=" * 80)
    print("\nThis test looks at ALL fields (not just title) to find Bulgarian content\n")
    
    topics = [
        "HORIZON-CL5-2026-03-D3-19",
        "HORIZON-CL5-2026-03-D3-20",
    ]
    
    for topic in topics:
        test_topic_deeply(topic)
        time.sleep(2)
    
    print("\n" + "=" * 80)
    print(" INTERPRETATION")
    print("=" * 80)
    
    print("""
If all fields show 🟢 (English):
  → The API is returning English content correctly
  → The earlier Bulgarian detection might have been in raw_json
  → Or the issue was with a different topic

If some fields show 🔴 (Bulgarian):
  → This is where the non-English content is coming from
  → We need to filter out results with Bulgarian in these fields
  → Solution: Update filter_bulgarian.py to check these specific fields

If raw_json shows 🔴 (Bulgarian):
  → The raw API response contains Bulgarian metadata
  → This is still mixed-language content
  → Client-side filtering will help
    """)


if __name__ == "__main__":
    main()
