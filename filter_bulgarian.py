#!/usr/bin/env python3
"""
Workaround: Post-process API results to filter out Bulgarian content
Since the API language filter doesn't work, we filter client-side
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def is_bulgarian_content(text: str) -> bool:
    """Detect if text contains Bulgarian (Cyrillic) characters"""
    if not text:
        return False
    
    # Check for Cyrillic Unicode range (0x0400-0x04FF)
    cyrillic_count = sum(1 for c in text if 0x0400 <= ord(c) <= 0x04FF)
    
    # If more than 5% of characters are Cyrillic, flag as Bulgarian
    if len(text) > 0:
        cyrillic_ratio = cyrillic_count / len(text)
        return cyrillic_ratio > 0.05
    
    return False


def filter_english_only(api_result: dict) -> dict | None:
    """
    Filter an API result, returning it only if content is primarily English.
    Returns None if Bulgarian content detected in key fields.
    
    Based on testing, Bulgarian content appears in:
    - title
    - summary
    - descriptionByte (via metadata.description)
    """
    if not api_result:
        return None
    
    md = api_result.get("metadata", {})
    
    # Critical fields that must be English (if present)
    critical_fields = [
        ("title", api_result.get("title")),
        ("summary", api_result.get("summary") or md.get("summary")),
        ("descriptionByte", md.get("descriptionByte") or md.get("description")),
    ]
    
    for field_name, value in critical_fields:
        # Handle list values (some API fields are lists)
        if isinstance(value, list):
            value = " ".join(str(v) for v in value if v)
        
        value = str(value or "")
        
        if value and is_bulgarian_content(value):
            # This result is Bulgarian - skip it
            return None
    
    # All critical fields are English (or empty) - keep this result
    return api_result


def filter_api_results(results: list) -> list:
    """Filter a list of API results, keeping only English content"""
    filtered = []
    
    for result in results:
        if filter_english_only(result):
            filtered.append(result)
    
    return filtered


# Example usage
if __name__ == "__main__":
    from modular.phase1_api import fetch_call_by_topic_id
    
    topics = [
        "HORIZON-CL5-2026-03-D3-19",
        "HORIZON-CL5-2026-03-D3-20",
    ]
    
    print("Testing client-side Bulgarian filtering:\n")
    
    for topic in topics:
        print(f"Topic: {topic}")
        
        result = fetch_call_by_topic_id(topic)
        
        if result:
            title = result.get("title")
            if isinstance(title, list):
                title = title[0] if title else "N/A"
            
            is_bulgarian = is_bulgarian_content(str(title or ""))
            
            filtered = filter_english_only(result)
            
            print(f"  Original: {title}")
            print(f"  Is Bulgarian: {is_bulgarian}")
            print(f"  Filtered out: {filtered is None}")
            print()
        else:
            print(f"  Not found\n")
