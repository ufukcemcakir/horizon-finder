# Horizon Finder - Flask Web Application

**A modern web-based EU Horizon Europe funding opportunity finder with AI-powered recommendations.**

## Architecture Redesign (Flask)

This is a complete rewrite of the Streamlit-based Horizon Finder, moving to a **Flask + vanilla JS** architecture for better control, reliability, and user experience.

### Overview

- **Framework**: Flask (Python web framework)
- **Frontend**: HTML5 + CSS3 + vanilla JavaScript (no heavy dependencies)
- **Backend**: Flask REST API
- **Database**: SQLite (local persistence)
- **LLM Integration**: Groq API (llama-3.3-70b-versatile)
- **PDF Processing**: pdfplumber

### Why Flask over Streamlit?

1. **Better Control**: Full control over HTTP request handling and session management
2. **API-Driven**: Clear separation between frontend and backend
3. **Reliability**: No Streamlit session state issues interfering with API calls
4. **Performance**: Faster and more lightweight
5. **Customization**: Complete freedom to design the UI/UX
6. **Scalability**: Can be deployed to production servers (Gunicorn, etc.)

### API Language Bug Fix

The original "Bulgarian language" issue was **NOT a request parameter problem**. 

**Root Cause Analysis:**
- The F&T Search API doesn't support language filtering via request parameters
- The `language` field in API responses is **metadata** about how calls are indexed in their database
- Removing the attempted `languages` parameter fix and reverting to the v6/v7 approach (no language specification)
- The actual content returned is in English, just with Bulgarian metadata tags

**Solution:** API now matches v6/v7 implementation with simple, direct calls without language parameters.

## File Structure

```
horizon/
├── app.py                          # Flask application entry point
├── requirements-flask.txt          # Python dependencies
├── templates/
│   ├── index.html                  # Main app shell (SPA)
│   ├── auth.html                   # Authentication page
│   └── [future: static pages]
├── static/                         # CSS, JS, images (future)
├── horizon.db                      # SQLite database
├── modular/                        # Core business logic (unchanged from v8)
│   ├── phase1_config.py            # Configuration constants
│   ├── phase1_db.py                # Database layer
│   ├── phase1_api.py               # F&T API integration (FIXED)
│   ├── phase1_pdf.py               # PDF parsing
│   ├── phase1_utils.py             # Utilities
│   ├── phase1_prefilter.py         # Call filtering
│   ├── phase1_agents.py            # Groq LLM agents
│   ├── phase1_validation.py        # Input validation
│   ├── phase1_errors.py            # Error handling
│   └── phase1_styles.py            # CSS/theming
└── pdfs/                           # Uploaded PDF storage
```

## Key Changes from Streamlit Version

### 1. **API-Driven Architecture**
- Streamlit: Page-based state management
- Flask: RESTful API endpoints, client renders HTML

### 2. **Session Management**
```python
# Flask sessions are simpler and don't interfere with HTTP requests
@app.route('/api/auth/login', methods=['POST'])
def api_login():
    user = do_login(email, password)
    if user:
        session['user_id'] = user[0]
        return jsonify({'success': True})
```

### 3. **Page Loading**
- Single Page Application (SPA) approach
- Pages loaded via `/api/page/<page_name>` endpoint
- JavaScript handles navigation without full page reloads

### 4. **Form Handling**
```javascript
// Simple fetch-based form submission
fetch('/api/auth/signup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, name, password })
})
```

## Installation & Setup

### 1. Create Virtual Environment
```bash
python -m venv venv
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r requirements-flask.txt
```

### 3. Set Environment Variables
```bash
# Create a .env file
export GROQ_API_KEY="your-groq-api-key"
export SECRET_KEY="your-secret-key-here"  # Optional, defaults to dev key
```

### 4. Initialize Database
```bash
python -c "from modular.phase1_db import init_db, migrate_db; init_db(); migrate_db()"
```

### 5. Run the Application
```bash
Flask:    python app.py
# OR
          flask run --host=127.0.0.1 --port=5000
```

Access the app at: **http://localhost:5000**

## API Endpoints

### Authentication
```
POST   /api/auth/signup      - Create new account
POST   /api/auth/login       - Sign in user
POST   /api/auth/logout      - Sign out user
GET    /api/auth/me          - Get current user info
```

### Calls (Horizon Opportunities)
```
GET    /api/calls/active              - List active calls
GET    /api/calls/<topic_id>          - Get call details
```

### Upload & Extraction
```
POST   /api/upload/extract-topics     - Extract topic IDs from PDF
POST   /api/upload/fetch-calls        - Fetch call details from API
```

### Profile
```
GET    /api/profile                   - Get organization profile
POST   /api/profile                   - Save organization profile
```

### Shortlist
```
GET    /api/shortlist                 - Get saved calls
POST   /api/shortlist/<topic_id>      - Add call to shortlist
```

### AI Recommendations
```
POST   /api/recommendations/screen    - Run screening agent
```

### Pages (Dynamic Content)
```
GET    /api/page/dashboard            - Dashboard content
GET    /api/page/discover             - Discover calls content
GET    /api/page/shortlist            - Shortlist content
GET    /api/page/profile              - Profile content
GET    /api/page/upload               - Upload content
GET    /api/page/recommendations      - Recommendations content
```

## Feature Completeness

### ✅ Implemented
- [x] User authentication (signup, login, logout)
- [x] Organization profile management
- [x] PDF upload and topic ID extraction
- [x] API call fetching (with fixed language handling)
- [x] Call browsing and filtering
- [x] Shortlist management
- [x] AI screening agent (Groq)
- [x] Database persistence (SQLite)
- [x] Session management
- [x] Validation layer
- [x] Error handling

### 🚀 Future Enhancements
- [ ] Advanced filtering (by date, status, budget)
- [ ] Saved searches
- [ ] Call comparison tool
- [ ] Export recommendations (PDF, CSV)
- [ ] Team collaboration features
- [ ] Multi-language UI support
- [ ] Dark/light theme toggle
- [ ] Mobile-responsive UI improvements

## Deployment

### Local Development
```bash
python app.py
```

### Production with Gunicorn
```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 app:app
```

### Docker
```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements-flask.txt .
RUN pip install -r requirements-flask.txt
COPY . .
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", "app:app"]
```

## Troubleshooting

### API Returns Wrong Language
- Solution: The F&T API doesn't support language filtering
- If you see Bulgarian metadata (`language: "bg"`), that's expected
- The actual content is in English - filter client-side if needed

### Session Not Persisting
- Clear browser cookies: `Settings → Privacy → Clear Site Data`
- Ensure `SECRET_KEY` environment variable is set

### PDF Upload Fails
- Check file is valid PDF (< 50MB)
- Ensure `pdfplumber` is installed correctly
- Check disk space in `pdfs/` folder

### Groq API Errors
- Verify `GROQ_API_KEY` is set correctly
- Check API key has not expired
- Monitor TPM/RPM rate limits in agent code

## Technology Stack Comparison

| Aspect | Streamlit | Flask |
|--------|-----------|-------|
| **Startup** | Slower, Python runtime | Fast |
| **Deployment** | Limited (Streamlit Cloud) | Any server (easy) |
| **Customization** | Limited | Full control |
| **Session State** | Can interfere with APIs | Clean separation |
| **Performance** | Slower reruns | Fast API calls |
| **Learning Curve** | Very easy | Moderate |
| **Production Ready** | No | Yes |

## Project Evolution

- **v1-v5**: Initial development with Streamlit
- **v6-v7**: Optimization and bug fixes (Streamlit)
- **v8 (Modular)**: Refactored into 11 modules, still Streamlit
- **Current (Flask)**: Complete framework redesign, API-driven architecture

## License

MIT License - Feel free to use and modify

## Support

For issues or questions, please refer to the modular package documentation in `/modular/README.md`

---

**Last Updated**: January 2025
**Status**: Production Ready
**Framework**: Flask 3.0.0
**Python Version**: 3.13+
