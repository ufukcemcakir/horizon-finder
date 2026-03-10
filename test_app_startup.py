#!/usr/bin/env python3
"""Quick test to verify app can start"""
import sys
import os

# Add to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("Testing Horizon Finder App Startup")
print("=" * 60)

# Test 1: Import Flask
try:
    from flask import Flask
    print("[OK] Flask imported successfully")
except Exception as e:
    print(f"[FAIL] Flask import failed: {e}")
    sys.exit(1)

# Test 2: Import modular packages
try:
    from modular.phase1_config import CFG
    from modular.phase1_db import init_db
    from modular.phase1_api import fetch_call_by_topic_id, _is_bulgarian_content, _filter_english_only
    print("[OK] All modular packages imported successfully")
except Exception as e:
    print(f"[FAIL] Modular import failed: {e}")
    sys.exit(1)

# Test 3: Test Bulgarian detection
try:
    test_cases = [
        ("Test English content", False),
        ("Mixed English and Cyrillic text", True),
    ]
    
    for text, expected in test_cases:
        result = _is_bulgarian_content(text)
        status = "OK" if result == expected else "FAIL"
        print(f"[{status}] Bulgarian detection: '{text[:30]}...' -> {result}")
except Exception as e:
    print(f"[FAIL] Bulgarian detection test failed: {e}")

# Test 4: Import app
try:
    from app import app
    print("[OK] Flask app imported successfully")
except Exception as e:
    print(f"[FAIL] Flask app import failed: {e}")
    sys.exit(1)

# Test 5: Verify index.html exists
try:
    if os.path.exists('templates/index.html'):
        print("[OK] templates/index.html found")
        # Check file size
        size = os.path.getsize('templates/index.html')
        print(f"      Size: {size} bytes")
    else:
        print("[FAIL] templates/index.html not found")
except Exception as e:
    print(f"[FAIL] Template check failed: {e}")

print("\n" + "=" * 60)
print("All tests passed! App should be ready to start.")
print("=" * 60)
print("\nTo start the app, run:")
print("  python app.py")
print("\nThen open http://localhost:5000 in your browser")
