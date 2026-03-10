# Quick Start Guide - Horizon Finder Flask App

## 🚀 Get Started in 3 Steps

### Step 1: Install PostgreSQL (One-Time Setup)

**Windows**:
1. Download: https://www.postgresql.org/download/windows/
2. Run installer, remember the password for `postgres` user
3. Install with defaults (port 5432)
4. Done!

**Create Database**:
```
createdb horizon
```

### Step 2: Configure Environment

Edit `.env` file in the `horizon` directory:

```
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/horizon
GROQ_API_KEY=gsk_your_api_key_here
FLASK_ENV=development
```

### Step 3: Start the App

```bash
cd c:\Users\ufuk.cakir\horizon
python app.py
```

Open http://localhost:5000 - you should see the login page!

---

## ✅ What's Fixed

- ✓ **Bulgarian Content Filtering**: Only English topics appear
- ✓ **App Startup**: No more blank screen - login page loads immediately
- ✓ **JavaScript**: All function conflicts resolved, proper initialization
- ✓ **Dependencies**: All packages installed and ready

---

## 📚 Documentation Files

- **`STATUS_REPORT.md`** - Complete summary of all changes
- **`APP_STARTUP_FIX_SUMMARY.md`** - Detailed fix explanations
- **`BULGARIAN_FILTERING_TECHNICAL_DOCS.md`** - How the filtering works
- **`test_app_startup.py`** - Diagnostic script to verify setup

---

## 🐛 Troubleshooting

### App won't start
```bash
# Check PostgreSQL is running
psql -U postgres -d horizon

# If error, update .env with correct credentials
```

### Blank page in browser
- Press F12 → Console tab
- Should see "AUTH response: 200" or similar
- Check `.env` has DATABASE_URL

### Bulgarian topics appearing
- This means your database still has old data
- Delete and re-upload: Clear calls db, re-upload work programme

---

## 💡 Next Steps After Starting

1. **Create Account**: Sign up on the login page
2. **Upload PDF**: Go to "Upload PDF" section
3. **Search Calls**: View available funding opportunities
4. **Shortlist Calls**: Save interesting opportunities
5. **Generate Ideas**: Use AI to generate contribution ideas

---

## 📞 Need Help?

1. Check **STATUS_REPORT.md** - Troubleshooting section
2. Open browser console (F12) for error messages
3. Check Flask terminal output for backend errors
4. Verify PostgreSQL connection: `psql -U postgres -d horizon`

---

## 🎯 Success Criteria

When app is working correctly:
- [ ] Flask starts with "Running on http://..." message
- [ ] Browser shows login page at http://localhost:5000
- [ ] Can create an account
- [ ] Can sign in successfully
- [ ] Navigation works without errors
- [ ] No JavaScript errors in browser console
- [ ] Bulgarian topics don't appear in search results

You're done! 🎉
