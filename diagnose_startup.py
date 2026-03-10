#!/usr/bin/env python3
"""Quick diagnostic to check if app can start"""

import sys
import os

# Add modular to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("Step 1: Checking imports...")
try:
    from modular.phase1_config import CFG
    print("  ✓ CFG imported")
except Exception as e:
    print(f"  ✗ CFG import failed: {e}")
    sys.exit(1)

try:
    from modular.phase1_db import init_db, migrate_db
    print("  ✓ phase1_db imported")
except Exception as e:
    print(f"  ✗ phase1_db import failed: {e}")
    sys.exit(1)

try:
    from modular.phase1_api import fetch_call_by_topic_id, parse_topic_json
    print("  ✓ phase1_api imported (with new Bulgarian filtering)")
except Exception as e:
    print(f"  ✗ phase1_api import failed: {e}")
    sys.exit(1)

try:
    from modular.phase1_pdf import extract_topic_ids_from_pdf
    print("  ✓ phase1_pdf imported")
except Exception as e:
    print(f"  ✗ phase1_pdf import failed: {e}")
    sys.exit(1)

try:
    from modular.phase1_agents import _get_groq_client, run_screening_agent
    print("  ✓ phase1_agents imported")
except Exception as e:
    print(f"  ✗ phase1_agents import failed: {e}")
    sys.exit(1)

try:
    from modular.phase1_prefilter import prefilter_calls
    print("  ✓ phase1_prefilter imported")
except Exception as e:
    print(f"  ✗ phase1_prefilter import failed: {e}")
    sys.exit(1)

print("\nStep 2: Testing Flask...")
try:
    from flask import Flask
    print("  ✓ Flask imported")
except Exception as e:
    print(f"  ✗ Flask import failed: {e}")
    sys.exit(1)

try:
    from flask_cors import CORS
    print("  ✓ CORS imported")
except Exception as e:
    print(f"  ✗ CORS import failed: {e}")
    sys.exit(1)

print("\nStep 3: Creating Flask app...")
try:
    app = Flask(__name__)
    print("  ✓ Flask app created")
except Exception as e:
    print(f"  ✗ Flask app creation failed: {e}")
    sys.exit(1)

print("\nStep 4: Testing DB initialization...")
try:
    print("  - Calling init_db()...")
    init_db()
    print("  ✓ init_db() completed")
except Exception as e:
    print(f"  ✗ init_db() failed: {e}")
    sys.exit(1)

try:
    print("  - Calling migrate_db()...")
    migrate_db()
    print("  ✓ migrate_db() completed")
except Exception as e:
    print(f"  ✗ migrate_db() failed: {e}")
    sys.exit(1)

print("\nStep 5: Testing API functions...")
try:
    print("  - Testing _filter_english_only()...")
    from modular.phase1_api import _filter_english_only
    
    # Test with fake result
    test_result = {
        "title": "Test English Title",
        "summary": "Test English Summary",
        "metadata": {"descriptionByte": "Test Description"}
    }
    filtered = _filter_english_only(test_result)
    if filtered:
        print("  ✓ _filter_english_only() works (English kept)")
    else:
        print("  ✗ _filter_english_only() filtered out English (bug!)")
except Exception as e:
    print(f"  ✗ API filter test failed: {e}")
    sys.exit(1)

print("\n" + "=" * 60)
print("✅ ALL CHECKS PASSED - App should start correctly")
print("=" * 60)
print("\nNow check:")
print("1. Browser: Open http://127.0.0.1:5000")
print("2. Browser F12 Console: Any errors?")
print("3. Browser Network tab: Which request is hanging?")
print("4. Terminal: Any error messages?")
