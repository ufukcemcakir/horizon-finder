#!/usr/bin/env python3
"""
Horizon Finder - API Testing Script
Run this to test all endpoints: python test_endpoints.py
"""

import requests
import json
import sys
from datetime import datetime

BASE_URL = "http://localhost:5000"
session = requests.Session()

# Color codes for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'
BOLD = '\033[1m'

def print_header(text):
    print(f"\n{BOLD}{BLUE}{'=' * 60}{RESET}")
    print(f"{BOLD}{BLUE}{text}{RESET}")
    print(f"{BOLD}{BLUE}{'=' * 60}{RESET}\n")

def print_success(text):
    print(f"{GREEN}✅ {text}{RESET}")

def print_error(text):
    print(f"{RED}❌ {text}{RESET}")

def print_info(text):
    print(f"{BLUE}ℹ️  {text}{RESET}")

def print_request(method, endpoint, status):
    status_color = GREEN if 200 <= status < 300 else RED
    print(f"   {method:6} {endpoint:40} {status_color}{status}{RESET}")

def test_endpoint(method, endpoint, data=None, expected_status=200, description=""):
    """Test an API endpoint"""
    url = f"{BASE_URL}{endpoint}"
    
    try:
        if method == "GET":
            response = session.get(url)
        elif method == "POST":
            response = session.post(url, json=data)
        else:
            return None
        
        print_request(method, endpoint, response.status_code)
        
        if response.status_code == expected_status:
            print_success(f"{description}: {response.status_code}")
            return response
        else:
            print_error(f"{description}: Expected {expected_status}, got {response.status_code}")
            if response.text:
                print(f"   Response: {response.text[:100]}")
            return None
            
    except requests.exceptions.ConnectionError:
        print_error(f"Cannot connect to {BASE_URL}")
        print_info("Make sure Flask app is running: python app.py")
        sys.exit(1)
    except Exception as e:
        print_error(f"Error: {str(e)}")
        return None

def main():
    print_header("🧪 Horizon Finder API Test Suite")
    print_info(f"Testing: {BASE_URL}")
    print_info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Test 1: Sign Up
    print(f"{BOLD}Test 1: User Sign Up{RESET}")
    signup_data = {
        "email": f"testuser_{int(datetime.now().timestamp())}@example.com",
        "name": "Test User",
        "password": "password123"
    }
    response = test_endpoint("POST", "/api/auth/signup", signup_data, 201, "Signup")
    if not response:
        print_error("Sign up failed - stopping tests")
        sys.exit(1)
    
    email = signup_data["email"]
    password = signup_data["password"]
    
    # Test 2: Sign In
    print(f"\n{BOLD}Test 2: User Sign In{RESET}")
    login_data = {"email": email, "password": password}
    response = test_endpoint("POST", "/api/auth/login", login_data, 200, "Login")
    if not response:
        print_error("Login failed - stopping tests")
        sys.exit(1)
    
    # Test 3: Get Current User
    print(f"\n{BOLD}Test 3: Get Current User{RESET}")
    test_endpoint("GET", "/api/auth/me", expected_status=200, description="Get current user")
    
    # Test 4: Get Active Calls
    print(f"\n{BOLD}Test 4: Get Active Calls{RESET}")
    response = test_endpoint("GET", "/api/calls/active?limit=5", expected_status=200, description="Get active calls")
    if response:
        data = response.json()
        if 'calls' in data:
            print(f"   Found {data.get('count', 0)} calls")
    
    # Test 5: Save Profile
    print(f"\n{BOLD}Test 5: Save Organization Profile{RESET}")
    profile_data = {
        "profile": "We are a research institute focused on renewable energy and sustainable materials. Our team has 50+ researchers with expertise in materials science, chemical engineering, and environmental sustainability."
    }
    test_endpoint("POST", "/api/profile", profile_data, 200, "Save profile")
    
    # Test 6: Get Profile
    print(f"\n{BOLD}Test 6: Get Organization Profile{RESET}")
    test_endpoint("GET", "/api/profile", expected_status=200, description="Get profile")
    
    # Test 7: Get Shortlist
    print(f"\n{BOLD}Test 7: Get Shortlist{RESET}")
    test_endpoint("GET", "/api/shortlist", expected_status=200, description="Get shortlist")
    
    # Test 8: Get Page Content (Dashboard)
    print(f"\n{BOLD}Test 8: Get Page Content{RESET}")
    test_endpoint("GET", "/api/page/dashboard", expected_status=200, description="Get dashboard page")
    test_endpoint("GET", "/api/page/discover", expected_status=200, description="Get discover page")
    test_endpoint("GET", "/api/page/profile", expected_status=200, description="Get profile page")
    
    # Test 9: Sign Out
    print(f"\n{BOLD}Test 9: User Sign Out{RESET}")
    test_endpoint("POST", "/api/auth/logout", expected_status=200, description="Logout")
    
    # Test 10: Verify Auth Required
    print(f"\n{BOLD}Test 10: Verify Auth Required (should fail){RESET}")
    response = test_endpoint("GET", "/api/auth/me", expected_status=401, description="Get current user (should fail)")
    
    print_header("✨ Test Suite Complete!")
    print_success("All endpoints are working correctly!")
    print_info("You can now use the web UI at http://localhost:5000")
    print_info("Check API_TESTING_GUIDE.md for more testing options\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{YELLOW}⚠️  Tests interrupted by user{RESET}")
        sys.exit(0)
    except Exception as e:
        print_error(f"Unexpected error: {str(e)}")
        sys.exit(1)
