# API Testing Guide

This guide shows you how to test the Horizon Finder API endpoints using different tools.

## Quick Start: Test the Endpoints Today

### Option 1: Using curl (Command Line)

Open your terminal and try these commands:

#### 1. **Sign Up**
```bash
curl -X POST http://localhost:5000/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "name": "Test User",
    "password": "password123"
  }'
```

**Expected Response:**
```json
{"success": true, "message": "Account created successfully."}
```

#### 2. **Sign In**
```bash
curl -X POST http://localhost:5000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "password123"
  }' \
  -c cookies.txt
```

**Expected Response:**
```json
{"success": true, "user": {"id": 1, "email": "test@example.com", "name": "Test User"}}
```

The `-c cookies.txt` saves the session cookie for subsequent requests.

#### 3. **Check Current User**
```bash
curl http://localhost:5000/api/auth/me \
  -b cookies.txt
```

**Expected Response:**
```json
{"authenticated": true, "user": {"id": 1, "email": "test@example.com", "name": "Test User"}}
```

#### 4. **Get Active Calls**
```bash
curl http://localhost:5000/api/calls/active \
  -b cookies.txt
```

#### 5. **Save Organization Profile**
```bash
curl -X POST http://localhost:5000/api/profile \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d '{
    "profile": "We are a research institute focused on renewable energy and sustainable materials development. Our team has 50+ researchers with expertise in materials science, chemical engineering, and environmental sustainability. We collaborate with universities across Europe and have published over 200 peer-reviewed papers."
  }'
```

#### 6. **Get Organization Profile**
```bash
curl http://localhost:5000/api/profile \
  -b cookies.txt
```

#### 7. **Get Shortlist**
```bash
curl http://localhost:5000/api/shortlist \
  -b cookies.txt
```

#### 8. **Sign Out**
```bash
curl -X POST http://localhost:5000/api/auth/logout \
  -b cookies.txt
```

---

### Option 2: Using Python (in a Python script)

Create a file called `test_api.py`:

```python
import requests
import json

BASE_URL = "http://localhost:5000"
session = requests.Session()

def test_sign_up():
    """Test user signup"""
    print("\n1️⃣  Testing Sign Up...")
    response = session.post(f"{BASE_URL}/api/auth/signup", json={
        "email": "testuser@example.com",
        "name": "Test User",
        "password": "password123"
    })
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    return response.status_code == 201

def test_sign_in():
    """Test user login"""
    print("\n2️⃣  Testing Sign In...")
    response = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": "testuser@example.com",
        "password": "password123"
    })
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    return response.status_code == 200

def test_get_current_user():
    """Test getting current user info"""
    print("\n3️⃣  Testing Get Current User...")
    response = session.get(f"{BASE_URL}/api/auth/me")
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    return response.status_code == 200

def test_save_profile():
    """Test saving organization profile"""
    print("\n4️⃣  Testing Save Profile...")
    response = session.post(f"{BASE_URL}/api/profile", json={
        "profile": "We are a research institute focused on renewable energy and sustainable materials development."
    })
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    return response.status_code == 200

def test_get_profile():
    """Test getting organization profile"""
    print("\n5️⃣  Testing Get Profile...")
    response = session.get(f"{BASE_URL}/api/profile")
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    return response.status_code == 200

def test_get_calls():
    """Test getting active calls"""
    print("\n6️⃣  Testing Get Active Calls...")
    response = session.get(f"{BASE_URL}/api/calls/active?limit=5")
    print(f"Status: {response.status_code}")
    data = response.json()
    print(f"Found {data.get('count', 0)} calls")
    if data.get('calls'):
        print(f"First call: {data['calls'][0]['title']}")
    return response.status_code == 200

def test_get_shortlist():
    """Test getting shortlist"""
    print("\n7️⃣  Testing Get Shortlist...")
    response = session.get(f"{BASE_URL}/api/shortlist")
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    return response.status_code == 200

def test_sign_out():
    """Test logout"""
    print("\n8️⃣  Testing Sign Out...")
    response = session.post(f"{BASE_URL}/api/auth/logout")
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    return response.status_code == 200

if __name__ == "__main__":
    print("🧪 Running API Tests...\n")
    print("=" * 50)
    
    try:
        test_sign_up()
        test_sign_in()
        test_get_current_user()
        test_save_profile()
        test_get_profile()
        test_get_calls()
        test_get_shortlist()
        test_sign_out()
        
        print("\n" + "=" * 50)
        print("✅ All tests completed!")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("Make sure the Flask app is running on http://localhost:5000")
```

Run it with:
```bash
python test_api.py
```

---

### Option 3: Using Postman (GUI Application)

1. **Download & Install Postman**: https://www.postman.com/downloads/
2. **Create a new request** in Postman
3. **Set up Sign Up request:**
   - Method: `POST`
   - URL: `http://localhost:5000/api/auth/signup`
   - Header: `Content-Type: application/json`
   - Body (JSON):
     ```json
     {
       "email": "test@example.com",
       "name": "Test User",
       "password": "password123"
     }
     ```
   - Click **Send**

4. **To maintain sessions in Postman:**
   - Go to Settings ⚙️ → Cookies → Add Domain
   - Postman will automatically save/send cookies with each request

---

### Option 4: Using Browser DevTools (Network Inspection)

1. **Open your browser** and go to `http://localhost:5000`
2. **Press F12** to open Developer Tools
3. **Click the "Network" tab**
4. **Try to sign up** via the web UI
5. **You'll see all HTTP requests** - check if POST to `/api/auth/signup` appears

**Expected network requests:**
- `GET /` (load page)
- `GET /api/auth/me` (check auth status)
- `POST /api/auth/signup` (signup request - should appear now)

---

## All Available Endpoints

### Authentication
```
POST   /api/auth/signup              Create account
POST   /api/auth/login               Sign in
POST   /api/auth/logout              Sign out
GET    /api/auth/me                  Get current user (requires auth)
```

### Calls
```
GET    /api/calls/active             List active calls (requires auth)
GET    /api/calls/<topic_id>         Get call details (requires auth)
```

### Profile
```
GET    /api/profile                  Get profile (requires auth)
POST   /api/profile                  Save profile (requires auth)
```

### Shortlist
```
GET    /api/shortlist                View shortlist (requires auth)
POST   /api/shortlist/<topic_id>     Add to shortlist (requires auth)
```

### Upload
```
POST   /api/upload/extract-topics    Extract topics from PDF (requires auth)
POST   /api/upload/fetch-calls       Fetch calls for topics (requires auth)
```

### Recommendations
```
POST   /api/recommendations/screen   Run screening agent (requires auth)
```

### Pages
```
GET    /api/page/dashboard           Dashboard page (requires auth)
GET    /api/page/discover            Discover page (requires auth)
GET    /api/page/shortlist           Shortlist page (requires auth)
GET    /api/page/profile             Profile page (requires auth)
GET    /api/page/upload              Upload page (requires auth)
GET    /api/page/recommendations     Recommendations page (requires auth)
```

---

## Debugging Tips

### 1. **Check Flask Console Output**
When you run `python app.py`, you'll see logs like:
```
127.0.0.1 - - [06/Mar/2026 13:34:26] "POST /api/auth/signup HTTP/1.1" 201 -
```

**Status codes:**
- `201` or `200`: Success ✅
- `400`: Bad request (check parameters)
- `401`: Unauthorized (need to sign in)
- `404`: Endpoint not found
- `500`: Server error (check Flask console for details)

### 2. **Enable Verbose curl**
```bash
curl -v -X POST http://localhost:5000/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","name":"Test","password":"pass123"}'
```

The `-v` flag shows all details including request headers and response.

### 3. **Print Debug Info in Flask Console**
All `console.log()` messages in the web app will appear in the browser DevTools console (F12).

### 4. **Use a REST Client Browser Extension**
- Chrome: "RESTClient" or "Insomnia"
- Firefox: "RESTClient"

---

## Common Issues & Solutions

### Issue: "Connection refused" or "Cannot reach localhost:5000"
**Solution:** Make sure Flask is running:
```bash
python app.py
# You should see: "Running on http://127.0.0.1:5000"
```

### Issue: "POST requests not being received"
This is what was happening to you! The fix is to ensure:
1. ✅ The form has `onsubmit="handleSignUp(event)"`
2. ✅ Event.preventDefault() is called
3. ✅ The fetch() request goes to the right URL
4. ✅ Headers include `'Content-Type': 'application/json'`

### Issue: CORS errors
If you see "CORS error" in DevTools:
- The Flask app already has `CORS(app)` enabled
- If still having issues, you can test with curl instead (bypasses CORS)

### Issue: "Invalid email" or validation errors
The email must:
- Have an @ symbol
- Have a domain (e.g., @example.com)
- Be under 254 characters

Passwords must be at least 6 characters.

---

## Next Steps

1. **Test with curl first** (simplest, no dependencies)
2. **Use Python script** for automated testing
3. **Use Postman** for interactive exploration
4. **Use Browser DevTools** to debug the web UI

Once tests pass, you'll know the API is working correctly and the issue was just the auth page not being properly bound to the form submission. The fixes I made should resolve this! 🎉
