#!/usr/bin/env python3
"""Test script to check F&T API language responses"""

import json
import requests
import sys
import os

# Add modular to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modular.phase1_config import CFG

def test_ft_api():
    """Test F&T API with language filters"""
    
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})
    
    # Sample topic IDs to test
    topic_ids = [
        "HORIZON-WIDERA-2024-ACCESS-02-01",
        "HORIZON-CL4-2024-RESILIENCE-01-01",
        "HORIZON-MISS-2024-CARE-01-01",
        "test-topic-123"
    ]
    
    print("=" * 80)
    print("F&T API Language Test")
    print("=" * 80)
    print(f"Base URL: {CFG.FT_SEARCH_BASE}")
    print(f"API Key: {CFG.API_KEY}")
    print()
    
    for topic_id in topic_ids:
        print(f"\n{'━' * 80}")
        print(f"Testing Topic ID: {topic_id}")
        print(f"{'━' * 80}")
        
        # Test 1: With language=["en"] filter
        print("\n[Test 1] With languages=['en'] filter:")
        try:
            r = session.post(
                f'{CFG.FT_SEARCH_BASE}?apiKey={CFG.API_KEY}&text="{topic_id}"',
                json={"pageNumber": 1, "pageSize": 3, "languages": ["en"]},
                timeout=20,
            )
            print(f"Status: {r.status_code}")
            
            if r.status_code == 200:
                data = r.json()
                results = data.get("results", [])
                print(f"Results found: {len(results)}")
                
                if results:
                    for i, result in enumerate(results):
                        print(f"\n  Result {i+1}:")
                        md = result.get("metadata", {})
                        
                        # Extract key fields
                        title = result.get("title")
                        if isinstance(title, list):
                            title = title[0] if title else None
                        
                        identifier = result.get("identifier") or result.get("callIdentifier")
                        if isinstance(identifier, list):
                            identifier = identifier[0] if identifier else None
                        
                        description = result.get("description") or md.get("description")
                        if isinstance(description, list):
                            description = description[0] if description else None
                        
                        print(f"    Identifier: {identifier}")
                        print(f"    Title: {title}")
                        print(f"    Title type: {type(title)}")
                        
                        if description:
                            desc_preview = str(description)[:100]
                            print(f"    Description (first 100 chars): {desc_preview}")
                            
                            # Try to detect language
                            if any(ord(char) > 127 for char in desc_preview):
                                print(f"    ⚠️  Contains non-ASCII characters (likely non-English)")
                            else:
                                print(f"    ✓ Appears to be ASCII (English-compatible)")
                else:
                    print("  No results found with language filter")
            else:
                print(f"Error: {r.text[:200]}")
                
        except Exception as e:
            print(f"  Exception: {e}")
        
        # Test 2: Without language filter
        print("\n[Test 2] Without language filter:")
        try:
            r = session.post(
                f'{CFG.FT_SEARCH_BASE}?apiKey={CFG.API_KEY}',
                json={"pageNumber": 1, "pageSize": 3, "text": topic_id},
                timeout=20,
            )
            print(f"Status: {r.status_code}")
            
            if r.status_code == 200:
                data = r.json()
                results = data.get("results", [])
                print(f"Results found: {len(results)}")
                
                if results:
                    # Check first result
                    result = results[0]
                    title = result.get("title")
                    if isinstance(title, list):
                        title = title[0] if title else None
                    
                    print(f"  First result title: {title}")
                    
                    # Check available languages in metadata
                    languages = result.get("metadata", {}).get("languages", [])
                    print(f"  Available languages: {languages}")
            else:
                print(f"Error: {r.text[:200]}")
                
        except Exception as e:
            print(f"  Exception: {e}")
    
    # Test 3: Check what parameters the API accepts
    print(f"\n{'━' * 80}")
    print("API Advanced Test - Parameter Check")
    print(f"{'━' * 80}")
    
    # Try different language format styles
    language_tests = [
        {"name": 'languages: ["en"]', "param": {"languages": ["en"]}},
        {"name": 'locale: "en"', "param": {"locale": "en"}},
        {"name": 'language: "en"', "param": {"language": "en"}},
        {"name": 'No language param', "param": {}},
    ]
    
    test_topic = "HORIZON-2024"
    
    for test in language_tests:
        print(f"\n[{test['name']}]")
        try:
            payload = {"pageNumber": 1, "pageSize": 1, "text": test_topic}
            payload.update(test["param"])
            
            r = session.post(
                f'{CFG.FT_SEARCH_BASE}?apiKey={CFG.API_KEY}',
                json=payload,
                timeout=10,
            )
            
            if r.status_code == 200:
                results = r.json().get("results", [])
                if results:
                    title = results[0].get("title")
                    if isinstance(title, list):
                        title = title[0]
                    print(f"  ✓ Returns result. Title sample: {str(title)[:60]}")
                else:
                    print(f"  No results")
            else:
                print(f"  Error {r.status_code}")
        except Exception as e:
            print(f"  Exception: {type(e).__name__}")
    
    print(f"\n{'═' * 80}")
    print("Test Complete")
    print(f"{'═' * 80}")


if __name__ == "__main__":
    test_ft_api()
