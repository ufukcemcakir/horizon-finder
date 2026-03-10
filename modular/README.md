# Horizon Finder - Modular Architecture Guide

## Overview

Horizon Finder is an AI-powered intelligence platform for EU Horizon Europe funding calls. The application has been refactored from a monolithic 1760-line script into a well-structured modular package with clear separation of concerns.

## Quick Start

```bash
# Set GROQ API key
export GROQ_API_KEY="your-key-here"

# Run the app
streamlit run phase1_v8.py
```

## Package Structure

```
modular/
├── __init__.py                 # Package initializer
├── phase1_config.py           # Configuration & constants
├── phase1_db.py              # Database layer (SQLite)
├── phase1_pdf.py             # PDF parsing (topic extraction)
├── phase1_api.py             # F&T Search API integration
├── phase1_utils.py           # Shared utilities & helpers
├── phase1_prefilter.py       # TF-IDF-style call filtering
├── phase1_validation.py      # Input validation (NEW)
├── phase1_errors.py          # Error handling & logging (NEW)
├── phase1_styles.py          # Theme & CSS management (NEW)
├── phase1_agents.py          # LLM agent orchestration (Groq)
└── phase1_ui.py              # Streamlit UI & page routing
```

## Module Documentation

### 🔧 Configuration (`phase1_config.py`)
Immutable configuration dataclass with all app settings:
- Database path
- API credentials
- Groq model settings
- UI defaults
- Validation rules

**Usage:**
```python
from modular.phase1_config import CFG
print(CFG.GROQ_MODEL)  # "llama-3.3-70b-versatile"
print(CFG.MIN_PASSWORD_LENGTH)  # 6
```

### 🗄️ Database (`phase1_db.py`)
SQLite database layer with schema management and CRUD operations:
- User authentication (signup/login)
- Organization profiles
- Call storage & retrieval
- Interest tracking (shortlists)
- Analysis caching
- Networking bookmarks

**Key Functions:**
```python
init_db()              # Initialize database schema
migrate_db()           # Run migrations
do_signup(email, name, password)
do_login(email, password)
save_calls(call_dicts)
load_org_profile(user_id)
```

### 📄 PDF Processing (`phase1_pdf.py`)
Extracts Horizon topic IDs from uploaded PDF files:
- Text extraction from multiple methods (text, words, tables)
- Regex-based topic ID identification
- Soft hyphen and formatting normalization

**Key Functions:**
```python
extract_topic_ids_from_pdf(file_stream) -> list[str]
```

### 🔗 API Integration (`phase1_api.py`)
Interfaces with F&T Search API to fetch call details:
- **Critical:** Requests English results via multipart form encoding
- Extracts metadata (title, deadline, scope, etc.)
- Handles API errors gracefully

**Key Functions:**
```python
fetch_call_by_topic_id(topic_id: str) -> Optional[dict]
parse_topic_json(query_key: str, result_item: dict) -> dict[str, str]
```

### ✅ Validation (`phase1_validation.py` - NEW)
Centralized input validation with consistent error messages:
- Email validation
- Password validation
- Name validation
- Organization profile validation
- Topic ID format validation

**Usage:**
```python
from modular.phase1_validation import Validator
is_valid, error = Validator.email("user@example.com")
if not is_valid:
    st.error(error)
```

### 🚨 Error Handling (`phase1_errors.py` - NEW)
Structured error handling and logging:
- Consistent error messages
- Logging with context
- Error decorators for functions
- Context managers for operations

**Usage:**
```python
from modular.phase1_errors import ErrorContext
with ErrorContext("call_fetch", "Failed to fetch calls"):
    # Code that might fail
    pass
```

### 🎨 Styling (`phase1_styles.py` - NEW)
Centralized CSS and theme management:
- Consistent color constants
- Color scheme (dark blue theme)
- Component styling (cards, buttons, pills, etc.)
- Easy theme switching in future

**Key Constants:**
```python
COLOR_PRIMARY = "#63b3ed"
COLOR_DARK_BG = "#0f1b35"
COLOR_SUCCESS = "#48bb78"
```

### 🔤 Utilities (`phase1_utils.py`)
Shared helper functions:
- String manipulation (`_trim`, `strip_html`)
- Input validation (`_is_valid_email`)
- Token estimation for LLM
- Keyword extraction from profiles
- HTML to text conversion

### 🎯 Pre-filtering (`phase1_prefilter.py`)
TF-IDF-style keyword scoring for initial call filtering:
- Keyword extraction from profile
- Bigram-based relevance scoring
- Top-K call selection

**Key Functions:**
```python
prefilter_calls(df, profile_text, top_k=60) -> pd.DataFrame
```

### 🤖 LLM Agents (`phase1_agents.py`)
Groq-powered intelligent call evaluation:
- **Screening Agent:** Batches calls and selects top candidates
- **Analysis Agent:** Deep-scores candidates against org profile
- **Contribution Idea Generator:** Generates proposal ideas
- **Rate Limiter:** TPM/RPM throttling for free tier

**Key Functions:**
```python
_get_groq_client() -> Optional[Groq]
run_screening_agent(df, profile, client, top_n) -> list[str]
run_analysis_agent(df, profile, phash, client) -> pd.DataFrame
groq_contribution_idea(call_row, profile_text) -> tuple[str, str]
```

### 🖥️ UI & Routing (`phase1_ui.py`)
Streamlit interface with 6 main pages:
1. **Auth**: Signup/login page
2. **Upload Programmes**: PDF upload & topic ID import
3. **Discover Calls**: Browse calls with filters
4. **Organization Profile**: Set up org description
5. **My Shortlist**: Saved calls management
6. **AI Recommendations**: Screening & analysis pipeline
7. **Networking**: Event/contact tracking

**Key Functions:**
```python
main()                     # Router & sidebar
page_auth()               # Authentication
page_upload()             # PDF/topic import
page_discover()           # Call browsing
page_profile()            # Profile setup
page_shortlist()          # Saved calls
page_recommend()          # AI screening
page_networking()         # Events & contacts
```

---

## Data Flow

```
User Opens App
    ↓
phase1_v8.py (entrypoint)
    ├─→ init_db() (phase1_db.py)
    ├─→ migrate_db() (phase1_db.py)
    └─→ main() (phase1_ui.py)
            ↓
        Session Validation
            ├─→ Not Logged In? → page_auth()
            └─→ Logged In? → PAGES[selected_page]()
                    ├─→ page_upload()
                    │   ├─→ extract_topic_ids_from_pdf() (phase1_pdf.py)
                    │   ├─→ fetch_call_by_topic_id() (phase1_api.py)
                    │   ├─→ parse_topic_json() (phase1_api.py)
                    │   └─→ save_calls() (phase1_db.py)
                    ├─→ page_discover()
                    │   ├─→ load_active_calls_df() (phase1_db.py)
                    │   └─→ add_interested() (phase1_db.py)
                    ├─→ page_profile()
                    │   ├─→ load_org_profile() (phase1_db.py)
                    │   └─→ save_org_profile() (phase1_db.py)
                    ├─→ page_recommend()
                    │   ├─→ prefilter_calls() (phase1_prefilter.py)
                    │   ├─→ run_screening_agent() (phase1_agents.py)
                    │   └─→ run_analysis_agent() (phase1_agents.py)
                    └─→ page_shortlist() / page_networking()
```

---

## Database Schema

```sql
-- Users
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Organization profiles
CREATE TABLE org_profile (
    user_id INTEGER PRIMARY KEY,
    profile_text TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Calls from F&T API
CREATE TABLE horizon_calls (
    topic_id TEXT PRIMARY KEY,
    title TEXT,
    call_description TEXT,
    summary TEXT,
    status TEXT,
    deadline TEXT,
    opening_date TEXT,
    type_of_action TEXT,
    programme_period TEXT,
    url TEXT,
    raw_json TEXT
);

-- User's shortlisted calls
CREATE TABLE user_interested_calls (
    user_id INTEGER,
    topic_id TEXT,
    created_at TEXT,
    PRIMARY KEY (user_id, topic_id)
);

-- LLM analysis results cache
CREATE TABLE analysis_cache (
    cache_key TEXT PRIMARY KEY,
    result TEXT,
    created_at TEXT
);

-- LLM screening results cache
CREATE TABLE screening_cache (
    cache_key TEXT PRIMARY KEY,
    result TEXT,
    created_at TEXT
);

-- Networking events & contacts
CREATE TABLE networking_bookmarks (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    title TEXT,
    link TEXT,
    event_date TEXT,
    notes TEXT,
    created_at TEXT
);
```

---

## Environment Variables

```bash
# Required for LLM features
export GROQ_API_KEY="gsk_xxxxxxxxxxxx"

# Optional (defaults provided in code)
export DB_PATH="horizon.db"
```

---

## Configuration Examples

**Increase screening batch size:**
```python
# In phase1_config.py
SCREENING_BATCH_SIZE: int = 50  # Default: 25
```

**Adjust LLM model:**
```python
# In phase1_config.py
GROQ_MODEL: str = "llama-3.3-70b-versatile"  # Default
# Available: check Groq API docs
```

**Change rate limits:**
```python
# In phase1_config.py
GROQ_TPM_LIMIT: int = 5500    # Tokens per minute
GROQ_RPM_LIMIT: int = 28       # Requests per minute
```

---

## Common Patterns

### Adding a New Page

1. Create page function in `phase1_ui.py`:
```python
def page_newpage() -> None:
    require_login()
    hero("New Page", "Description")
    # Your UI code here
```

2. Add to PAGES dict:
```python
PAGES = {
    ...
    "New Page": page_newpage,
}
```

3. Function is automatically available in sidebar navigation.

### Adding Validation

1. Add method to `Validator` class:
```python
@staticmethod
def my_field(value: str) -> Tuple[bool, Optional[str]]:
    if not value:
        return False, "Field is required"
    return True, None
```

2. Use in UI:
```python
is_valid, error = Validator.my_field(user_input)
if not is_valid:
    st.error(error)
```

### Adding Error Handling

1. Use context manager:
```python
from modular.phase1_errors import ErrorContext

with ErrorContext("operation_name", "User-friendly message"):
    risky_operation()
```

2. Or use decorator:
```python
from modular.phase1_errors import with_error_handling

@with_error_handling(
    default_return=None,
    user_message="Failed to complete operation"
)
def my_function():
    pass
```

---

## Testing

**Quick validation:**
```bash
# Check Python syntax
python -m py_compile modular/*.py

# Run type checker (if mypy configured)
mypy modular/
```

**Manual testing workflow:**
1. Create test account
2. Upload test PDF with topic IDs
3. Verify API returns English results
4. Test AI recommendations (requires GROQ_API_KEY)
5. Check database persistence

---

## Troubleshooting

### API returns Russian results
- **Cause:** API doesn't recognize language parameter
- **Fix:** Check `phase1_api.py` uses multipart `files` encoding
- **Update:** Verify `languages: ["en"]` is sent

### GROQ API rate limiting
- **Cause:** Too many requests or tokens
- **Fix:** Increase `GROQ_DELAY_SEC` in config or reduce `SCREENING_BATCH_SIZE`
- **Monitor:** Check rate limiter logs

### Database locked
- **Cause:** Concurrent access or incomplete transaction
- **Fix:** Ensure DB operations use proper context managers
- **Future:** Implement connection pooling

---

## Performance Tips

1. **Batch LLM calls** - Screening agent processes multiple calls per request
2. **Cache profiles** - Session state caches sidebar stats
3. **Prefilter before analysis** - Reduce calls sent to expensive analysis agent
4. **Use API delay** - Build in sleep between API calls (0.1s default)

---

## Future Enhancements

- [ ] Dark/light mode toggle
- [ ] Database connection pooling
- [ ] Redis caching layer
- [ ] User preferences storage
- [ ] Advanced analytics dashboard
- [ ] Data export (CSV/JSON)
- [ ] Multi-language support
- [ ] Funding database integrations

---

## Contributing

To add a new feature:
1. Determine which module it belongs in
2. Follow existing code style and patterns
3. Add type hints
4. Add docstrings
5. Update this README if needed

---

**Package Version:** 1.0  
**Last Updated:** 2026-03-06  
**Status:** Production Ready (Core Features Complete)
