"""
Horizon Finder - Flask Web Application
Entry point for the Flask app instead of Streamlit
"""

from flask import Flask, render_template, request, jsonify, session, send_from_directory, send_file
from flask_cors import CORS
import json
import os
import sys

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except:
    pass  # dotenv not required, but helpful for development

# Add modular package to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modular.phase1_config import CFG
from modular.phase1_db import (
    init_db, migrate_db, do_signup, do_login, load_org_profile, save_org_profile,
    load_active_calls_df, load_calls_df, get_existing_topic_ids, save_calls,
    add_interested, get_interested_calls, remove_interested, clear_interested,
    save_contribution, get_contribution
)
from modular.phase1_api import fetch_call_by_topic_id, parse_topic_json, fetch_open_grant_calls, parse_api_call
from modular.phase1_pdf import extract_topic_ids_from_pdf
from modular.phase1_utils import _is_valid_email
from modular.phase1_agents import _get_groq_client, run_screening_agent, run_analysis_agent, groq_contribution_idea
from modular.phase1_prefilter import prefilter_calls

# Initialize Flask app
app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = os.environ.get('SECRET_KEY', 'horizon-finder-dev-key-change-in-production')
CORS(app)

# Initialize database on startup
try:
    init_db()
    migrate_db()
except Exception as e:
    import traceback
    print(f"[ERROR] Database initialization failed: {e}")
    traceback.print_exc()
    raise

# ═══════════════════════════════════════════════════════════════════════════════
# AUTH ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/auth/signup', methods=['POST'])
def api_signup():
    """Sign up a new user"""
    data = request.json
    email = data.get('email', '').strip().lower()
    name = data.get('name', '').strip()
    password = data.get('password', '')
    
    if not email or not name:
        return jsonify({'error': 'Email and name are required.'}), 400
    if not _is_valid_email(email):
        return jsonify({'error': 'Please enter a valid email address.'}), 400
    if len(password) < CFG.MIN_PASSWORD_LENGTH:
        return jsonify({'error': f'Password must be at least {CFG.MIN_PASSWORD_LENGTH} characters.'}), 400
    
    result = do_signup(email, name, password)
    if result:
        return jsonify({'success': True, 'message': 'Account created successfully.'}), 201
    else:
        return jsonify({'error': 'An account with this email already exists.'}), 409


@app.route('/api/auth/login', methods=['POST'])
def api_login():
    """Log in a user"""
    data = request.json
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    
    if not email or not password:
        return jsonify({'error': 'Email and password are required.'}), 400
    
    user = do_login(email, password)
    if user:
        session['user_id'] = user[0]
        session['user_email'] = user[1]
        session['user_name'] = user[2]
        return jsonify({
            'success': True,
            'user': {'id': user[0], 'email': user[1], 'name': user[2]}
        }), 200
    else:
        return jsonify({'error': 'Invalid email or password.'}), 401


@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    """Log out a user"""
    session.clear()
    return jsonify({'success': True}), 200


@app.route('/api/auth/me', methods=['GET'])
def api_me():
    """Get current user info"""
    if 'user_id' not in session:
        return jsonify({'authenticated': False}), 401
    
    return jsonify({
        'authenticated': True,
        'user': {
            'id': session.get('user_id'),
            'email': session.get('user_email'),
            'name': session.get('user_name')
        }
    }), 200


# ═══════════════════════════════════════════════════════════════════════════════
# UPLOAD & FETCH ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/upload/extract-topics', methods=['POST'])
def api_extract_topics():
    """Extract topic IDs from uploaded PDF"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided.'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected.'}), 400
    
    try:
        topic_ids = extract_topic_ids_from_pdf(file)
        existing = get_existing_topic_ids()
        to_fetch = [t for t in topic_ids if t not in existing]
        
        return jsonify({
            'success': True,
            'found': topic_ids,
            'new': to_fetch,
            'count': len(topic_ids)
        }), 200
    except Exception as e:
        return jsonify({'error': f'Failed to parse PDF: {str(e)}'}), 400


@app.route('/api/upload/fetch-calls', methods=['POST'])
def api_fetch_calls():
    """Fetch calls for found topics"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401
    
    data = request.json
    topic_ids = data.get('topics', [])
    
    if not topic_ids:
        return jsonify({'error': 'No topics provided.'}), 400
    
    calls = []
    failed = []
    
    for tid in topic_ids:
        try:
            item = fetch_call_by_topic_id(tid)
            if item:
                call = parse_topic_json(tid, item)
                calls.append(call)
        except Exception as e:
            failed.append({'topic_id': tid, 'error': str(e)})
    
    saved, save_failed = save_calls(calls)
    
    return jsonify({
        'success': True,
        'saved': saved,
        'failed': len(failed) + len(save_failed),
        'errors': failed + save_failed
    }), 200


@app.route('/api/calls/fetch-from-api', methods=['POST'])
def api_fetch_from_eu():
    """Fetch all open grant calls from EU Funding & Tenders API"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401
    
    try:
        # Fetch calls from EU API
        api_calls = fetch_open_grant_calls(page_size=50, language='en', sleep_s=0.1)
        
        if not api_calls:
            return jsonify({'error': 'No calls found from API'}), 400
        
        # Parse and prepare calls for database
        parsed_calls = []
        failed_parse = 0
        for i, api_item in enumerate(api_calls):
            try:
                call = parse_api_call(api_item)
                if call.get('topic_id'):
                    parsed_calls.append(call)
                else:
                    print(f"[WARN] Call {i} has no topic_id: {call}")
                    failed_parse += 1
            except Exception as e:
                print(f"[ERROR] Parsing call {i}: {e}")
                failed_parse += 1
                continue
        
        print(f"[DEBUG] Parsed {len(parsed_calls)} calls, {failed_parse} failed to parse")
        
        # Save to database
        saved, save_failed = save_calls(parsed_calls)
        
        print(f"[DEBUG] Saved {saved} calls, {len(save_failed)} save failures")
        if save_failed:
            print(f"[DEBUG] Sample failures: {save_failed[:3]}")
        
        return jsonify({
            'success': True,
            'fetched': len(api_calls),
            'parsed': len(parsed_calls),
            'saved': saved,
            'failed': len(save_failed),
            'message': f'Successfully saved {saved} calls from EU API'
        }), 200
    
    except Exception as e:
        print(f"[ERROR] API fetch error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Failed to fetch from API: {str(e)}'}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# DISCOVER ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/calls/active', methods=['GET'])
def api_active_calls():
    """Get active calls with optional filtering"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401
    
    df = load_active_calls_df()
    
    # Optional filters
    keyword = request.args.get('keyword', '').lower()
    status_filter = request.args.get('status', '')
    limit = int(request.args.get('limit', 50))
    
    if keyword:
        df = df[df.apply(lambda r: keyword in (str(r.get('title', '') + r.get('call_description', ''))).lower(), axis=1)]
    
    if status_filter:
        df = df[df['status'] == status_filter]
    
    result = df.head(limit).to_dict('records')
    return jsonify({'success': True, 'calls': result, 'count': len(result)}), 200


@app.route('/api/debug/calls-count', methods=['GET'])
def debug_calls_count():
    """Debug endpoint: show total calls and sample statuses"""
    from modular.phase1_db import db_query
    
    # Get total count
    total = db_query("SELECT COUNT(*) FROM horizon_calls") or [(0,)]
    total_count = total[0][0] if total else 0
    
    # Get sample statuses
    statuses = db_query("SELECT DISTINCT status FROM horizon_calls ORDER BY status LIMIT 10") or []
    status_list = [s[0] for s in statuses]
    
    # Count by status
    status_counts = db_query(
        "SELECT status, COUNT(*) as cnt FROM horizon_calls GROUP BY status ORDER BY cnt DESC"
    ) or []
    
    return jsonify({
        'total_calls': total_count,
        'sample_statuses': status_list,
        'status_distribution': [{'status': s[0], 'count': s[1]} for s in status_counts]
    }), 200


@app.route('/api/calls/<topic_id>', methods=['GET'])
def api_call_detail(topic_id):
    """Get call details"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401
    
    df = load_calls_df()
    call = df[df['topic_id'] == topic_id].to_dict('records')
    
    if call:
        return jsonify({'success': True, 'call': call[0]}), 200
    else:
        return jsonify({'error': 'Call not found.'}), 404


@app.route('/api/calls/search', methods=['GET'])
def api_search_calls():
    """Search and filter calls by keyword, cluster, status, deadline, budget."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401
    
    df = load_calls_df()
    
    # Get filters from query params
    keyword = request.args.get('keyword', '').lower()
    cluster = request.args.get('cluster', '').lower()
    status_filter = request.args.get('status', '').lower()
    deadline_before = request.args.get('deadline_before', '')
    deadline_after = request.args.get('deadline_after', '')
    budget_min = request.args.get('budget_min', '')
    limit = int(request.args.get('limit', 100))
    
    # Filter by keyword
    if keyword:
        df = df[df.apply(lambda r: keyword in (str(r.get('title', '') + ' ' + r.get('call_description', ''))).lower(), axis=1)]
    
    # Filter by cluster
    if cluster:
        df = df[df['cluster'].fillna('').str.lower().str.contains(cluster, na=False)]
    
    # Filter by status
    if status_filter:
        df = df[df['status'].fillna('').str.lower() == status_filter]
    
    # Filter by deadline
    if deadline_before:
        df = df[df['deadline'] <= deadline_before]
    if deadline_after:
        df = df[df['deadline'] >= deadline_after]
    
    # Filter by budget (simple text match)
    if budget_min:
        df = df[df['budget'].fillna('').str.lower().str.contains(budget_min.lower(), na=False)]
    
    result = df.head(limit).to_dict('records')
    return jsonify({
        'success': True,
        'calls': result,
        'count': len(result),
        'total': len(df)
    }), 200


@app.route('/api/contributions/list', methods=['GET'])
def api_list_contributions():
    """List all saved contribution ideas for the current user."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401
    
    from modular.phase1_db import db_query
    
    rows = db_query(
        "SELECT c.topic_id, c.idea_text, c.created_at, h.title, h.cluster "
        "FROM contributions c "
        "JOIN horizon_calls h ON h.topic_id = c.topic_id "
        "WHERE c.user_id = %s "
        "ORDER BY c.created_at DESC",
        (session['user_id'],)
    ) or []
    
    ideas = [
        {
            'topic_id': r[0],
            'idea_text': r[1][:200] + '...' if len(r[1]) > 200 else r[1],  # Preview
            'created_at': r[2],
            'call_title': r[3],
            'cluster': r[4]
        }
        for r in rows
    ]
    
    return jsonify({'success': True, 'ideas': ideas, 'count': len(ideas)}), 200


# ═══════════════════════════════════════════════════════════════════════════════
# PROFILE ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/profile', methods=['GET'])
def api_get_profile():
    """Get organization profile"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401
    
    profile = load_org_profile(session['user_id'])
    return jsonify({'success': True, 'profile': profile}), 200


@app.route('/api/profile', methods=['POST'])
def api_save_profile():
    """Save organization profile"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401
    
    data = request.json
    profile_text = data.get('profile', '').strip()
    
    if not profile_text:
        return jsonify({'error': 'Profile text is required.'}), 400
    
    # The profile can be any length since it's now structured data
    save_org_profile(session['user_id'], profile_text)
    return jsonify({'success': True, 'message': 'Profile saved.'}), 200


# ═══════════════════════════════════════════════════════════════════════════════
# SHORTLIST ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/shortlist', methods=['GET'])
def api_get_shortlist():
    """Get user's shortlist"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401
    
    df = get_interested_calls(session['user_id'])
    calls = df.to_dict('records') if not df.empty else []
    return jsonify({'success': True, 'calls': calls}), 200


@app.route('/api/shortlist/<topic_id>', methods=['POST'])
def api_add_to_shortlist(topic_id):
    """Add call to shortlist"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401
    
    add_interested(session['user_id'], topic_id)
    return jsonify({'success': True, 'message': f'Added {topic_id} to shortlist.'}), 200


@app.route('/api/shortlist/<topic_id>', methods=['DELETE'])
def api_remove_from_shortlist(topic_id):
    """Remove call from shortlist"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401
    
    remove_interested(session['user_id'], topic_id)
    return jsonify({'success': True, 'message': f'Removed {topic_id} from shortlist.'}), 200


@app.route('/api/shortlist/clear/all', methods=['DELETE'])
def api_clear_all_shortlist():
    """Clear all calls from shortlist"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401
    
    clear_interested(session['user_id'])
    return jsonify({'success': True, 'message': 'Shortlist cleared.'}), 200


# ═══════════════════════════════════════════════════════════════════════════════
# AI RECOMMENDATION ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/recommendations/screen', methods=['POST'])
def api_screen_calls():
    """Run screening agent on calls with optional cluster/deadline filters"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401
    
    data = request.json or {}
    top_n = data.get('top_n', 10)
    clusters = data.get('clusters', [])  # List of cluster names to filter
    deadline_before = data.get('deadline_before', '')  # YYYY-MM-DD format
    deadline_after = data.get('deadline_after', '')  # YYYY-MM-DD format
    
    profile = load_org_profile(session['user_id'])
    if not profile:
        return jsonify({'error': 'Please set up your profile first.'}), 400
    
    df = load_active_calls_df()
    if df.empty:
        return jsonify({'error': 'No active calls available.'}), 400
    
    # Apply cluster filter if specified
    if clusters and len(clusters) > 0:
        df = df[df['cluster'].fillna('').isin(clusters)]
    
    # Apply deadline filters if specified
    if deadline_before:
        df = df[df['deadline'] <= deadline_before]
    if deadline_after:
        df = df[df['deadline'] >= deadline_after]
    
    if df.empty:
        return jsonify({'error': 'No calls match the specified filters.'}), 400
    
    # Pre-filter
    suggested = prefilter_calls(df, profile)
    
    client = _get_groq_client()
    if not client:
        return jsonify({'error': 'GROQ_API_KEY not set.'}), 503
    
    try:
        tids = run_screening_agent(suggested, profile, client, top_n=top_n)

        # Return detailed call objects for the frontend to render
        calls_df = load_calls_df()
        selected_calls = []
        for t in tids:
            row = calls_df[calls_df['topic_id'] == t]
            if not row.empty:
                r = row.iloc[0].to_dict()
                selected_calls.append({
                    'topic_id': r.get('topic_id'),
                    'title': r.get('title'),
                    'status': r.get('status'),
                    'deadline': r.get('deadline'),
                    'opening_date': r.get('opening_date', None),
                    'type_of_action': r.get('type_of_action'),
                    'summary': r.get('call_description') or r.get('summary') or '',
                    'url': r.get('url'),
                    'cluster': r.get('cluster'),
                    'budget': r.get('budget')
                })

        return jsonify({'success': True, 'selected': selected_calls}), 200
    except Exception as e:
        return jsonify({'error': f'Screening failed: {str(e)}'}), 500


@app.route('/api/calls/<topic_id>/contribution', methods=['POST'])
def api_generate_contribution(topic_id):
    """Generate a contribution idea for a specific call using the LLM helper"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401

    profile = load_org_profile(session['user_id'])
    if not profile:
        return jsonify({'error': 'Please set up your profile first.'}), 400

    calls_df = load_calls_df()
    row = calls_df[calls_df['topic_id'] == topic_id]
    if row.empty:
        return jsonify({'error': 'Call not found.'}), 404

    call_row = row.iloc[0].to_dict()

    client = _get_groq_client()
    if not client:
        return jsonify({'error': 'GROQ_API_KEY not set.'}), 503

    try:
        # groq_contribution_idea returns (prompt, content)
        _, idea_text = groq_contribution_idea(call_row, profile)
        return jsonify({'success': True, 'idea': idea_text}), 200
    except Exception as e:
        return jsonify({'error': f'Contribution generation failed: {str(e)}'}), 500


@app.route('/api/calls/<topic_id>/contribution/save', methods=['POST'])
def api_save_contribution(topic_id):
    """Save a generated contribution idea to the DB for the current user."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401

    data = request.json or {}
    idea = data.get('idea', '')
    if not idea or not idea.strip():
        return jsonify({'error': 'Idea text required.'}), 400

    try:
        save_contribution(session['user_id'], topic_id, idea)
        return jsonify({'success': True, 'message': 'Idea saved.'}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to save idea: {e}'}), 500


@app.route('/api/calls/<topic_id>/contribution', methods=['GET'])
def api_get_contribution(topic_id):
    """Retrieve a saved contribution idea for the current user."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated.'}), 401

    try:
        from modular.phase1_db import get_contribution
        idea = get_contribution(session['user_id'], topic_id)
        if idea:
            return jsonify({'success': True, 'idea': idea}), 200
        else:
            return jsonify({'success': False, 'error': 'No saved idea found.'}), 404
    except Exception as e:
        return jsonify({'error': f'Failed to retrieve idea: {e}'}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# FRONTEND ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    """Serve main index page"""
    return render_template('index.html')

@app.route('/templates/<path:filename>')
def serve_template(filename):
    """Serve individual templates for loading"""
    try:
        return send_from_directory('templates', filename)
    except:
        return jsonify({'error': 'Template not found'}), 404

@app.route('/api/page/<page_name>')
def api_page(page_name):
    """Load page content dynamically"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    pages = {
        'dashboard': render_dashboard,
        'discover': render_discover,
        'shortlist': render_shortlist,
        'profile': render_profile,
        'upload': render_upload,
        'recommendations': render_recommendations,
        'contributions': render_contributions,
    }
    
    if page_name not in pages:
        return jsonify({'error': 'Page not found'}), 404
    
    try:
        html = pages[page_name]()
        return jsonify({'html': html})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE RENDERERS
# ═══════════════════════════════════════════════════════════════════════════════

def render_dashboard():
    """Dashboard page content"""
    df = load_active_calls_df()
    profile = load_org_profile(session['user_id'])
    
    html = f"""
    <div class="page-header">
        <h1 class="page-title">📊 Dashboard</h1>
        <p class="page-desc">Welcome to Horizon Finder. Manage your calls and recommendations.</p>
    </div>
    
    <div class="card">
        <h2 class="card-title">Quick Stats</h2>
        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.5rem;">
            <div style="text-align: center;">
                <div style="font-size: 2rem; font-weight: 700; color: #63b3ed;">{len(df)}</div>
                <div style="color: #7a90a8; font-size: 0.85rem;">Active Calls</div>
            </div>
            <div style="text-align: center;">
                <div style="font-size: 2rem; font-weight: 700; color: #48bb78;">{len(get_interested_calls(session['user_id']))}</div>
                <div style="color: #7a90a8; font-size: 0.85rem;">Shortlisted</div>
            </div>
            <div style="text-align: center;">
                <div style="font-size: 2rem; font-weight: 700; color: #ed8936;">{'Yes' if profile else 'No'}</div>
                <div style="color: #7a90a8; font-size: 0.85rem;">Profile Set</div>
            </div>
        </div>
    </div>
    
    <div class="card">
        <h2 class="card-title">Getting Started</h2>
        <ol style="padding-left: 1.5rem; color: #90aecb;">
            <li style="margin-bottom: 1rem;"><strong>Set Your Profile:</strong> Go to "My Profile" and describe your organization to get better recommendations.</li>
            <li style="margin-bottom: 1rem;"><strong>Upload a PDF:</strong> Upload a Horizon Europe call list or RFP document to extract topic IDs.</li>
            <li style="margin-bottom: 1rem;"><strong>Discover Calls:</strong> Browse active funding opportunities and add interesting ones to your shortlist.</li>
            <li><strong>Get Recommendations:</strong> Run AI-powered screening to find the best matching calls for your organization.</li>
        </ol>
    </div>
    """
    return html

def render_discover():
    """Discover calls page content"""
    df = load_active_calls_df().head(50)
    
    html = """
    <div class="page-header">
        <h1 class="page-title">🔍 Discover Calls</h1>
        <p class="page-desc">Browse active Horizon 2024-2025 funding opportunities</p>
    </div>
    
    <div class="card">
        <div style="margin-bottom: 1.5rem; display: flex; justify-content: space-between; align-items: center;">
            <div style="flex: 1;">
                <input type="text" id="search-calls" placeholder="Search calls by keyword..." 
                       style="width: 100%; padding: 0.75rem;">
            </div>
            <button class="btn btn-danger" data-action="clearAllDiscoverCalls" style="margin-left: 0.75rem;">Clear All</button>
        </div>
        
        <div id="calls-list">
    """
    
    if df.empty:
        html += '<p style="color: #7a90a8; text-align: center; padding: 2rem;">No calls available yet. Upload a PDF to get started.</p>'
    else:
        for _, call in df.iterrows():
            status_class = f"badge-{call['status'].lower()}" if call['status'].lower() in ['open', 'archival', 'closed'] else 'badge-closed'
            html += f"""
            <div style="padding: 1rem; border-bottom: 1px solid rgba(99,179,237,0.1);">
                <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 0.5rem;">
                    <div style="cursor: pointer; flex: 1;" class="call-item" data-action="viewCallDetail" data-topic-id="{call['topic_id']}">
                        <div style="font-size: 0.75rem; color: #63b3ed; font-family: monospace; font-weight: 600;">{call['topic_id']}</div>
                        <div style="font-size: 0.95rem; font-weight: 600; color: #e2eaf6; margin-top: 0.25rem;">{call.get('title', 'Untitled')}</div>
                    </div>
                    <span class="badge {status_class}">{call.get('status', 'Unknown')}</span>
                </div>
                <div style="font-size: 0.85rem; color: #90aecb;">Deadline: <span class="formatted-date">{call.get('deadline', 'N/A')}</span></div>
                <div style="margin-top: 0.75rem; display: flex; gap: 0.5rem;">
                    <button class="btn btn-secondary btn-small call-action" data-action="addToShortlist" data-topic-id="{call['topic_id']}">+ Shortlist</button>
                    <button class="btn btn-danger btn-small" data-action="removeFromDiscoverList" data-topic-id="{call['topic_id']}">✕ Remove</button>
                </div>
            </div>
            """
    
    html += """
        </div>
    </div>
    """
    return html

def render_shortlist():
    """Shortlist page content"""
    df = get_interested_calls(session['user_id'])
    
    html = """
    <div class="page-header">
        <h1 class="page-title">⭐ My Shortlist</h1>
        <p class="page-desc">Your saved calls and opportunities</p>
    </div>
    
    <div class="card">
    """
    
    if df.empty:
        html += '<p style="color: #7a90a8; text-align: center; padding: 2rem;">Your shortlist is empty. Add calls from the Discover page.</p>'
    else:
        html += f"""
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
            <p style="color: #90aecb;">You have {len(df)} calls in your shortlist.</p>
            <button class="btn btn-danger btn-small" data-action="clearAllShortlist">Clear All</button>
        </div>
        """
        html += '<table style="width: 100%; border-collapse: collapse;"><thead><tr><th style="text-align: left; padding: 0.75rem; border-bottom: 1px solid rgba(99,179,237,0.2);">Topic ID</th><th style="text-align: left; padding: 0.75rem; border-bottom: 1px solid rgba(99,179,237,0.2);">Title</th><th style="text-align: left; padding: 0.75rem; border-bottom: 1px solid rgba(99,179,237,0.2);">Status</th><th style="text-align: left; padding: 0.75rem; border-bottom: 1px solid rgba(99,179,237,0.2);">Deadline</th><th style="text-align: center; padding: 0.75rem; border-bottom: 1px solid rgba(99,179,237,0.2);">Action</th></tr></thead><tbody>'
        
        for _, call in df.iterrows():
            has_idea = str(call.get('has_idea', False)) == 'True' or call.get('has_idea')
            html += f"""
            <tr>
                <td style="padding: 0.75rem; border-bottom: 1px solid rgba(99,179,237,0.1);"><code style="color: #63b3ed;">{call['topic_id']}</code></td>
                <td style="padding: 0.75rem; border-bottom: 1px solid rgba(99,179,237,0.1); cursor: pointer;" class="call-item" data-action="viewCallDetail" data-topic-id="{call['topic_id']}">{call.get('title', 'Untitled')}
                    {'<span style="margin-left:0.5rem; padding:0.15rem 0.5rem; border-radius:6px; font-size:0.75rem; background:rgba(72,187,120,0.12); color:var(--success); font-weight:700;">✓ Idea saved</span>' if has_idea else ''}
                </td>
                <td style="padding: 0.75rem; border-bottom: 1px solid rgba(99,179,237,0.1);"><span class="badge badge-{call['status'].lower() if call['status'].lower() in ['open', 'forthcoming'] else 'closed'}">{call['status']}</span></td>
                <td style="padding: 0.75rem; border-bottom: 1px solid rgba(99,179,237,0.1);"><span class="formatted-date">{call.get('deadline', 'N/A')}</span></td>
                <td style="padding: 0.75rem; border-bottom: 1px solid rgba(99,179,237,0.1); text-align: center;">
                    <div style="display:flex; gap:0.5rem; justify-content:center; align-items:center; flex-wrap:wrap;">
                        {f'<button class="btn btn-secondary btn-small" data-action="viewContribution" data-topic-id="{call["topic_id"]}">View Idea</button>' if has_idea else ''}
                        <button class="btn btn-primary btn-small" data-action="generateContribution" data-topic-id="{call['topic_id']}">Generate Contribution</button>
                        <button class="btn btn-danger btn-small" data-action="removeFromShortlist" data-topic-id="{call['topic_id']}">✕</button>
                    </div>
                </td>
            </tr>
            """
        
        html += '</tbody></table>'
    
    html += '</div>'
    return html

def render_profile():
    """Profile page content"""
    profile_json = load_org_profile(session['user_id'])
    profile = {}
    if profile_json:
        try:
            profile = json.loads(profile_json) if isinstance(profile_json, str) else profile_json
        except (json.JSONDecodeError, TypeError):
            # Fallback for old plain-text format
            profile = {"text": profile_json}
    
    html = f"""
    <div class="page-header">
        <h1 class="page-title">👤 Organization Profile</h1>
        <p class="page-desc">Tell us about your organization to get better personalized recommendations</p>
    </div>
    
    <div class="card">
        <form id="profile-form" class="page-form" data-form-type="profile">
            <div class="form-group">
                <label>Organization Name</label>
                <input type="text" name="org_name" id="profile-org-name" placeholder="Your organization's name"
                       value="{profile.get('org_name', '')}" required>
            </div>
            
            <div class="form-group">
                <label>Organization Type</label>
                <select name="org_type" id="profile-org-type">
                    <option value="">Select...</option>
                    <option value="research" {"selected" if profile.get('org_type') == 'research' else ""}>Research Institution</option>
                    <option value="sme" {"selected" if profile.get('org_type') == 'sme' else ""}>SME</option>
                    <option value="large" {"selected" if profile.get('org_type') == 'large' else ""}>Large Enterprise</option>
                    <option value="nonprofit" {"selected" if profile.get('org_type') == 'nonprofit' else ""}>Non-Profit / NGO</option>
                    <option value="government" {"selected" if profile.get('org_type') == 'government' else ""}>Government / Public</option>
                </select>
            </div>
            
            <div class="form-group">
                <label>Core Research/Business Areas</label>
                <textarea name="competencies" id="profile-competencies" placeholder="e.g., AI/Machine Learning, Biotechnology, Green Energy, etc."
                          style="height: 120px; resize: vertical;">{profile.get('competencies', '')}</textarea>
                <small style="color: #7a90a8;">Separate multiple areas with commas</small>
            </div>
            
            <div class="form-group">
                <label>Past Experiences & Key Projects</label>
                <textarea name="past_experiences" id="profile-experiences" placeholder="Brief description of completed projects, achievements, or relevant experience"
                          style="height: 120px; resize: vertical;">{profile.get('past_experiences', '')}</textarea>
            </div>
            
            <div class="form-group">
                <label>Technical Expertise & Keywords</label>
                <textarea name="technical_expertise" id="profile-expertise" placeholder="e.g., Quantum Computing, Data Analytics, Blockchain, IoT sensors, etc."
                          style="height: 100px; resize: vertical;">{profile.get('technical_expertise', '')}</textarea>
            </div>
            
            <div class="form-group">
                <label>Partnerships & Collaborations (Optional)</label>
                <textarea name="partnerships" id="profile-partnerships" placeholder="Current or desired partnerships with other organizations"
                          style="height: 80px; resize: vertical;">{profile.get('partnerships', '')}</textarea>
            </div>
            
            <button type="submit" class="btn btn-primary">Save Profile</button>
        </form>
    </div>
    """
    return html

def render_upload():
    """Upload PDF page content"""
    html = """
    <div class="page-header">
        <h1 class="page-title">� Import Calls</h1>
        <p class="page-desc">Fetch the latest Horizon Europe grant opportunities from the EU API</p>
    </div>
    
    <div class="card">
        <h2 class="card-title">🌐 Fetch from EU API</h2>
        <p style="color: #90aecb; margin-bottom: 1.5rem;">
            Automatically fetch all open Horizon Europe grant calls directly from the official EU Funding & Tenders API. 
            Calls are automatically categorized into their respective clusters.
        </p>
        <button class="btn btn-primary btn-large" id="fetch-api-btn" style="padding: 1rem 2rem; font-size: 1rem;">
            🚀 Fetch Latest Calls from EU API
        </button>
        <div id="api-fetch-status" style="margin-top: 1.5rem;"></div>
    </div>
    
    <div class="card">
        <h2 class="card-title">📄 Alternative: Upload PDF</h2>
        <p style="color: #90aecb; margin-bottom: 1.5rem;">
            If you have a specific Horizon Europe work programme PDF, you can upload it to extract topic IDs manually.
        </p>
        <div id="drop-zone" class="upload-drop-zone" style="border: 2px dashed rgba(99,179,237,0.3); border-radius: 8px; padding: 2rem; 
                    text-align: center; cursor: pointer;">
            <div style="font-size: 2rem; margin-bottom: 1rem;">📤</div>
            <div style="font-weight: 600; color: #e2eaf6; margin-bottom: 0.5rem;">Drop PDF here or click to upload</div>
            <div style="font-size: 0.85rem; color: #7a90a8;">Supported: PDF files up to 50MB</div>
            <input type="file" id="pdf-input" class="pdf-input" accept=".pdf" style="display: none;">
        </div>
        
        <div id="upload-status" style="margin-top: 1.5rem;"></div>
    </div>
    """
    return html
    return html

def render_recommendations():
    """Recommendations page content with filters for cluster and deadline"""
    # Get unique clusters from DB
    from modular.phase1_db import db_query
    clusters_rows = db_query("SELECT DISTINCT cluster FROM horizon_calls WHERE cluster IS NOT NULL AND cluster != '' ORDER BY cluster") or []
    clusters = [r[0] for r in clusters_rows if r[0]]
    
    cluster_options = '\n'.join([f'<option value="{c.strip()}">{c.strip()}</option>' for c in clusters if c])
    
    html = f"""
    <div class="page-header">
        <h1 class="page-title">🤖 AI Recommendations</h1>
        <p class="page-desc">Get personalized call recommendations using AI screening</p>
    </div>
    
    <div class="card">
        <p style="margin-bottom: 1.5rem; color: #90aecb;">
            This tool uses AI to screen active calls against your organization profile 
            and find the best matching opportunities.
        </p>
        
        <div style="margin-bottom: 1.5rem; padding: 1rem; background: rgba(99,179,237,0.05); border-radius: 8px;">
            <h3 style="margin-bottom: 1rem; font-size: 1rem;">Filters (Optional)</h3>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1rem;">
                <div>
                    <label style="display: block; font-size: 0.85rem; margin-bottom: 0.5rem;">Cluster</label>
                    <select id="rec-cluster-filter" style="width: 100%; padding: 0.5rem; background: var(--card); border: 1px solid rgba(99,179,237,0.2); border-radius: 6px; color: #e2eaf6;">
                        <option value="">All Clusters</option>
                        {cluster_options}
                    </select>
                </div>
                <div>
                    <label style="display: block; font-size: 0.85rem; margin-bottom: 0.5rem;">Deadline After</label>
                    <input type="date" id="rec-deadline-after" style="width: 100%; padding: 0.5rem; background: var(--card); border: 1px solid rgba(99,179,237,0.2); border-radius: 6px; color: #e2eaf6;">
                </div>
                <div>
                    <label style="display: block; font-size: 0.85rem; margin-bottom: 0.5rem;">Deadline Before</label>
                    <input type="date" id="rec-deadline-before" style="width: 100%; padding: 0.5rem; background: var(--card); border: 1px solid rgba(99,179,237,0.2); border-radius: 6px; color: #e2eaf6;">
                </div>
            </div>
        </div>
        
        <button class="btn btn-primary" id="run-screening-btn" data-action="runScreening" title="Run AI screening against your profile" aria-label="Run AI screening">Run Screening Agent</button>
        <div id="recommendations-status" style="margin-top: 1.5rem;"></div>
    </div>
    """
    return html

def render_contributions():
    """Contribution ideas page content"""
    html = """
    <div class="page-header">
        <h1 class="page-title">💡 My Contribution Ideas</h1>
        <p class="page-desc">View and manage your saved contribution ideas for calls</p>
    </div>
    
    <div class="card">
        <div id="contributions-list" style="margin-top: 1rem;">
            <div class="spinner"></div> Loading ideas...
        </div>
    </div>
    
    <script>
        // Load contributions list on page load
        if (currentPage === 'contributions') {
            loadContributionsList();
        }
    </script>
    """
    return html

if __name__ == '__main__':
    # use_reloader=False to prevent Flask watchdog from hanging on database reinit
    app.run(debug=True, host='127.0.0.1', port=5000, use_reloader=False)
