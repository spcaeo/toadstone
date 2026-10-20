import sys
import threading
import time
import csv
import json
import xml.etree.ElementTree as ET
import uuid
import webbrowser
import argparse
import secrets
import string
import os
import base64
from io import StringIO, BytesIO
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
from flask_compress import Compress
from functools import wraps
from src.crawler import WebCrawler
from src.settings_manager import SettingsManager
from src.auth_db import init_db, create_user, authenticate_user, get_user_by_id, log_guest_crawl, get_guest_crawls_last_24h, verify_user, set_user_tier, create_verification_token, verify_token, get_user_by_email
from src.email_service import send_verification_email, send_welcome_email

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Parse command line arguments
parser = argparse.ArgumentParser(description='Toadstone - SEO Spider Tool')
parser.add_argument('--local', '-l', action='store_true',
                    help='Run in local mode (all users get admin tier, no rate limits)')
parser.add_argument('--disable-register', '-dr', action='store_true',
                    help='Disable new user registrations')
parser.add_argument('--disable-guest', '-dg', action='store_true',
                    help='Disable guest login')
parser.add_argument('--demo', '-dm', action='store_true',
                    help='Demo mode: 1.5GB memory limit per user, crawls auto-stop at limit')
parser.add_argument('--dangerously-skip-auth', '-dsa', action='store_true',
                    help='DANGEROUS: Allow anyone to log in as any username with no password. '
                         'The username is only used to separate per-user sessions. '
                         'Do NOT use on a public network or in production.')
parser.add_argument('--crawl', metavar='URL',
                    help='Crawl URL once and exit instead of starting the server (for CI)')
parser.add_argument('--fail-on', choices=['error', 'warning', 'info'], default=None,
                    help='With --crawl: exit non-zero when an issue of this severity or worse is open')
parser.add_argument('--report', metavar='FILE',
                    help='With --crawl: write the issues to FILE as JSON')
parser.add_argument('--port', '-p', type=int, default=None,
                    help='Port to serve on (default 5000, or the PORT environment '
                         'variable). macOS AirPlay Receiver owns 5000 by default, '
                         'so a Mac usually needs this.')
args = parser.parse_args()

LOCAL_MODE = args.local or os.getenv('LOCAL_MODE', '').lower() in ('true', '1', 'yes')
DISABLE_REGISTER = args.disable_register or os.getenv('REGISTRATION_DISABLED', '').lower() in ('true', '1', 'yes')
DISABLE_GUEST = args.disable_guest or os.getenv('DISABLE_GUEST', '').lower() in ('true', '1', 'yes')
DEMO_MODE = args.demo or os.getenv('DEMO_MODE', '').lower() in ('true', '1', 'yes')
SKIP_AUTH = args.dangerously_skip_auth or os.getenv('DANGEROUSLY_SKIP_AUTH', '').lower() in ('true', '1', 'yes')
PORT = args.port or int(os.getenv('PORT') or 5000)

app = Flask(__name__, template_folder='web/templates', static_folder='web/static')

# In local mode the app is a development tool: pick up edited templates without
# a restart. Jinja caches compiled templates otherwise, so a changed page keeps
# serving the old markup until the process dies.
if os.getenv('LOCAL_MODE', '').lower() in ('true', '1', 'yes') or '--local' in sys.argv or '-l' in sys.argv:
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.jinja_env.auto_reload = True
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
if not os.environ.get('SECRET_KEY'):
    print('⚠️  WARNING: SECRET_KEY not set — using an ephemeral random key. '
          'Sessions will not persist across restarts. Set SECRET_KEY in production.', flush=True)

# Enable compression for all responses
Compress(app)

# Ensure data directory exists before initializing the database
os.makedirs("data", exist_ok=True)

# Initialize database on startup
init_db()

def generate_random_password(length=16):
    """Generate a random password with letters, digits, and symbols"""
    alphabet = string.ascii_letters + string.digits + string.punctuation
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def auto_login_local_mode():
    """Auto-login for local mode - creates or logs into 'local' admin account"""
    import sqlite3
    try:
        conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'users.db'))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check if 'local' user exists
        cursor.execute('SELECT id, username, tier FROM users WHERE username = ?', ('local',))
        user = cursor.fetchone()

        if user:
            # User exists, just log them in
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['tier'] = 'admin'
            session.permanent = True
            print(f"Auto-logged in as existing 'local' user (ID: {user['id']})")
        else:
            # Create new local user with random password
            random_password = generate_random_password()
            from src.auth_db import hash_password
            password_hash = hash_password(random_password)

            cursor.execute('''
                INSERT INTO users (username, email, password_hash, verified, tier)
                VALUES (?, ?, ?, 1, 'admin')
            ''', ('local', 'local@localhost', password_hash))
            conn.commit()

            user_id = cursor.lastrowid

            # Log in the new user
            session['user_id'] = user_id
            session['username'] = 'local'
            session['tier'] = 'admin'
            session.permanent = True

            print(f"Created and auto-logged in as new 'local' admin user (ID: {user_id})")
            print(f"Generated password: {random_password}")

        conn.close()
        return True
    except Exception as e:
        print(f"Error in auto_login_local_mode: {e}")
        return False

def skip_auth_login(username):
    """Skip-auth login: create user record if missing, log them in.

    Each username gets its own user_id, which drives per-user crawler
    instance and settings isolation. No password is checked. Always
    grants admin tier (matches local-mode behavior).

    Returns (success, message).
    """
    import sqlite3
    try:
        conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'users.db'))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('SELECT id, username FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()

        if user:
            user_id = user['id']
        else:
            from src.auth_db import hash_password
            random_password = generate_random_password()
            password_hash = hash_password(random_password)
            cursor.execute('''
                INSERT INTO users (username, email, password_hash, verified, tier)
                VALUES (?, ?, ?, 1, 'admin')
            ''', (username, f'{username}@skipauth.local', password_hash))
            conn.commit()
            user_id = cursor.lastrowid

        conn.close()

        session['user_id'] = user_id
        session['username'] = username
        session['tier'] = 'admin'
        session.permanent = True

        return True, 'Logged in (authentication skipped)'
    except sqlite3.IntegrityError as e:
        # Most likely the generated email collides with an existing account
        # whose email happens to match. Fall back to a clearer message.
        return False, f'Username conflict: try a different username ({e})'
    except Exception as e:
        print(f"Error in skip_auth_login: {e}")
        return False, f'Login error: {str(e)}'

if LOCAL_MODE:
    print("=" * 60)
    print("LOCAL MODE ENABLED")
    print("All users will have admin tier access")
    print("No rate limits or tier restrictions")
    print("Auto-login enabled with 'local' admin account")
    print("=" * 60)

if DISABLE_REGISTER:
    print("=" * 60)
    print("REGISTRATION DISABLED")
    print("New user registrations are not allowed")
    print("=" * 60)

if DISABLE_GUEST:
    print("=" * 60)
    print("GUEST MODE DISABLED")
    print("Guest login is not allowed")
    print("=" * 60)

if DEMO_MODE:
    print("=" * 60)
    print("DEMO MODE ENABLED")
    print("Memory limit: 1.5GB per user")
    print("Crawls will auto-stop when limit is reached")
    print("=" * 60)

if SKIP_AUTH:
    print("=" * 60)
    print("⚠️  DANGEROUSLY SKIP AUTH ENABLED")
    print("Anyone can log in as any username with no password!")
    print("Username is used only to separate per-user sessions.")
    print("DO NOT use on a public network or production server!")
    print("=" * 60)

def get_client_ip():
    """Get the real client IP address, checking Cloudflare headers first"""
    # Check Cloudflare header first
    if 'CF-Connecting-IP' in request.headers:
        return request.headers['CF-Connecting-IP']
    # Check other common proxy headers
    if 'X-Forwarded-For' in request.headers:
        # X-Forwarded-For can contain multiple IPs, take the first one
        return request.headers['X-Forwarded-For'].split(',')[0].strip()
    if 'X-Real-IP' in request.headers:
        return request.headers['X-Real-IP']
    # Fall back to direct connection IP
    return request.remote_addr

def login_required(f):
    """Decorator to require login for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # In local mode, auto-login if not already logged in
        if LOCAL_MODE and 'user_id' not in session:
            auto_login_local_mode()
        elif 'user_id' not in session:
            # Not in local mode and not logged in
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

# Reporting API (issues, pages, links, graph, sitemaps, diffs). It lives in its
# own module and takes the decorator above, so auth stays defined in one place.
from src.api.reporting import create_blueprint as _create_reporting_blueprint  # noqa: E402

app.register_blueprint(_create_reporting_blueprint(login_required))

# Exports: every dataset as CSV/JSON/NDJSON/XLSX, a workbook or zip of the
# lot, and a self-contained report a human can send on
from src.api.exports import create_blueprint as _create_exports_blueprint  # noqa: E402

app.register_blueprint(_create_exports_blueprint(login_required))

# The check reference every issue's docs_url points at
from src.api.checks import create_blueprint as _create_checks_blueprint  # noqa: E402

app.register_blueprint(_create_checks_blueprint(login_required))

# Server log analysis. The module has existed, with four log formats and bot
# verification, since it was written - and nothing called it. No route, no
# crawl hook, nothing: a capability the project claimed and could not reach.
from src.api.logs import create_blueprint as _create_logs_blueprint  # noqa: E402

from src import crawl_db as _crawl_db  # noqa: E402

# The fixer agent. Two of the four agent kinds the package documents - fixer
# and monitor - were imported by nothing at all; this is the fixer's route.
from src.api.fixer_routes import create_blueprint as _create_fixer_blueprint  # noqa: E402

app.register_blueprint(_create_fixer_blueprint(
    login_required,
    lambda crawl_id: _crawl_db.load_crawl_issues(crawl_id) or [],
    lambda: get_session_settings().get_crawler_config()))

app.register_blueprint(_create_logs_blueprint(
    login_required,
    lambda crawl_id: _crawl_db.load_crawled_urls(crawl_id) or [],
    _crawl_db.save_issues_batch))

# Connecting a Google account by clicking a button, rather than by pasting
# a refresh token into a form.
from src.api.google_routes import create_blueprint as _create_google_blueprint  # noqa: E402

app.register_blueprint(_create_google_blueprint(
    login_required, lambda: app.secret_key,
    lambda: getattr(get_or_create_crawler(), 'base_url', '') or ''))

# A crawl thread has no app context but still has to decrypt the connected
# account, so the store is told how to find the secret once, here.
from src.core import token_store as _token_store  # noqa: E402

_token_store.bind_secret(lambda: app.secret_key)

# The link-graph visualisation and the memory diagnostics.
from src.api.diagnostics import create_blueprint as _create_diagnostics_blueprint  # noqa: E402

app.register_blueprint(_create_diagnostics_blueprint(
    login_required, lambda: get_or_create_crawler(),
    lambda: (crawler_instances, instances_lock)))

# Settings, pause/resume, and the crawls already on disk.
from src.api.session_routes import create_blueprint as _create_session_blueprint  # noqa: E402

app.register_blueprint(_create_session_blueprint(
    login_required, lambda: get_or_create_crawler(),
    lambda: get_session_settings(), DEMO_MODE))

# Signing in and signing up, plus the three modes that bypass all of it.
from src.api.auth_routes import create_blueprint as _create_auth_blueprint  # noqa: E402

app.register_blueprint(_create_auth_blueprint(
    {'LOCAL_MODE': LOCAL_MODE, 'SKIP_AUTH': SKIP_AUTH,
     'DISABLE_REGISTER': DISABLE_REGISTER, 'DISABLE_GUEST': DISABLE_GUEST},
    {'login_required': login_required,
     'get_client_ip': lambda *a: get_client_ip(*a),
     'auto_login_local_mode': lambda *a: auto_login_local_mode(*a),
     'skip_auth_login': lambda *a: skip_auth_login(*a),
     'send_verification_email': lambda *a: send_verification_email(*a),
     'send_welcome_email': lambda *a: send_welcome_email(*a)}))

# The original export endpoints. src/api/exports/ is the one to use, but
# these are the shapes the UI and existing scripts already call.
from src.api.legacy_exports import create_blueprint as _create_legacy_exports  # noqa: E402
from src.api.legacy_exports import (  # noqa: E402,F401
    XLSX_MIMETYPE, issue_matches_exclusion)

app.register_blueprint(_create_legacy_exports(
    login_required, lambda: get_or_create_crawler(),
    lambda: get_session_settings(),
    lambda *a: filter_issues_by_exclusion_patterns(*a)))

# The MCP server, so an AI agent can drive a crawl through the same API a
# person uses. Mounted here as well as over stdio; both reach Toadstone as
# the signed-in user, so an agent sees exactly the crawls its caller could.
from src.mcp.http_app import create_blueprint as _create_mcp_blueprint  # noqa: E402

app.register_blueprint(_create_mcp_blueprint(login_required))

# Scheduled crawls. The loop owns no crawling of its own: a job that fires
# starts an ordinary crawl, so the diff, the exports and the webhook all
# work on it without knowing it was scheduled.
from src.api.schedules import create_blueprint as _create_schedules_blueprint  # noqa: E402
from src.core import scheduler as _scheduler  # noqa: E402

_scheduler_loop = None


def _run_scheduled_crawl(job):
    """Start one scheduled crawl and return its id."""
    from src.crawler import WebCrawler
    from src.settings_manager import SettingsManager

    crawler = WebCrawler()
    manager = SettingsManager()
    manager.tier = 'admin'  # a job has no browser session to take a tier from
    try:
        config = manager.get_crawler_config()
        config.update(_scheduler.job_settings(job))
        crawler.update_config(config)
    except Exception as e:
        print(f'Could not apply settings for schedule {job.get("id")}: {e}')

    ok, message = crawler.start_crawl(job['url'], user_id=job.get('user_id'))
    if not ok:
        raise RuntimeError(message)
    return crawler.crawl_id


app.register_blueprint(_create_schedules_blueprint(
    login_required, runner=_run_scheduled_crawl,
    get_scheduler=lambda: _scheduler_loop))


def _start_scheduler():
    """Bring the timer up once the app is ready."""
    global _scheduler_loop
    if _scheduler_loop:
        return _scheduler_loop
    _scheduler_loop = _scheduler.Scheduler(_run_scheduled_crawl)
    _scheduler_loop.start()
    return _scheduler_loop


# Multi-tenant crawler instances
crawler_instances = {}  # session_id -> {'crawler': WebCrawler, 'settings': SettingsManager, 'last_accessed': datetime}
instances_lock = threading.Lock()

def get_or_create_crawler():
    """Get or create a crawler instance for the current session"""
    # Get or create session ID
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())

    session_id = session['session_id']
    user_id = session.get('user_id')  # Get user_id from session
    tier = session.get('tier', 'guest')  # Get tier from session

    with instances_lock:
        # Check if crawler exists for this session
        if session_id not in crawler_instances:
            print(f"Creating new crawler instance for session: {session_id}, user: {user_id}, tier: {tier}")
            crawler_instances[session_id] = {
                'crawler': WebCrawler(),
                'settings': SettingsManager(session_id=session_id, user_id=user_id, tier=tier),  # Per-user settings
                'last_accessed': datetime.now()
            }
        else:
            # Update last accessed time
            crawler_instances[session_id]['last_accessed'] = datetime.now()

        return crawler_instances[session_id]['crawler']

def get_session_settings():
    """Get the settings manager for the current session"""
    # Get or create session ID
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())

    session_id = session['session_id']
    user_id = session.get('user_id')  # Get user_id from session
    tier = session.get('tier', 'guest')  # Get tier from session

    with instances_lock:
        # Create instance if it doesn't exist
        if session_id not in crawler_instances:
            print(f"Creating new settings instance for session: {session_id}, user: {user_id}, tier: {tier}")
            crawler_instances[session_id] = {
                'crawler': WebCrawler(),
                'settings': SettingsManager(session_id=session_id, user_id=user_id, tier=tier),
                'last_accessed': datetime.now()
            }
        else:
            # Update last accessed time
            crawler_instances[session_id]['last_accessed'] = datetime.now()

        return crawler_instances[session_id]['settings']

def cleanup_old_instances():
    """Remove crawler instances that haven't been accessed in 1 hour"""
    timeout = timedelta(hours=1)
    now = datetime.now()

    with instances_lock:
        sessions_to_remove = []
        for session_id, instance_data in crawler_instances.items():
            if now - instance_data['last_accessed'] > timeout:
                sessions_to_remove.append(session_id)

        for session_id in sessions_to_remove:
            print(f"Cleaning up crawler instance for session: {session_id}")
            # Stop any running crawls
            try:
                crawler_instances[session_id]['crawler'].stop_crawl()
            except:
                pass
            del crawler_instances[session_id]

        if sessions_to_remove:
            print(f"Cleaned up {len(sessions_to_remove)} inactive crawler instances")

def start_cleanup_thread():
    """Start background thread to cleanup old instances"""
    def cleanup_loop():
        while True:
            time.sleep(300)  # Check every 5 minutes
            try:
                cleanup_old_instances()
            except Exception as e:
                print(f"Error in cleanup thread: {e}")

    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()
    print("Started crawler instance cleanup thread")

def filter_issues_by_exclusion_patterns(issues, exclusion_patterns):
    """Filter issues based on exclusion patterns (applies current settings to loaded crawls)"""
    if not exclusion_patterns:
        return issues
    return [issue for issue in issues if not issue_matches_exclusion(issue, exclusion_patterns)]

def get_exclusion_patterns(settings_manager):
    """Current issue-exclusion patterns as a list of non-empty lines"""
    current_settings = settings_manager.get_settings()
    exclusion_patterns_text = current_settings.get('issueExclusionPatterns', '')
    return [p.strip() for p in exclusion_patterns_text.split('\n') if p.strip()]


    return json.dumps({
        'export_date': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total_issues': len(issues),
        'total_urls_with_issues': len(issues_by_url),
        'issues_by_url': issues_by_url,
        'all_issues': issues
    }, indent=2)

@app.route('/favicon.ico')
def favicon():
    """Browsers ask for /favicon.ico whatever the page declares"""
    return send_from_directory(app.static_folder, 'favicon.svg', mimetype='image/svg+xml')


@app.route('/')
@app.route('/crawl/<int:crawl_id>')
@app.route('/crawl/<int:crawl_id>/<tab>')
def index(crawl_id=None, tab=None):
    # Deep links (/crawl/<id>/<tab>?filter=...) are the same single-page app;
    # web/static/js/router.js reads the URL and restores that view
    # In local mode, auto-login if not already logged in
    if LOCAL_MODE and 'user_id' not in session:
        auto_login_local_mode()
    elif 'user_id' not in session:
        # Not in local mode and not logged in: remember the deep link so the
        # user lands back on it after signing in
        if request.path != '/' or request.query_string:
            session['next_path'] = request.full_path
        return redirect(url_for('login_page'))

    next_path = session.pop('next_path', None)
    if next_path and next_path.startswith('/') and not next_path.startswith('//'):
        return redirect(next_path)
    return render_template('index.html')

@app.route('/dashboard')
@login_required
def dashboard():
    """Crawl history dashboard"""
    return render_template('dashboard.html')

@app.route('/debug/memory')
@login_required
def debug_memory_page():
    """Debug page with nice UI for memory monitoring"""
    return render_template('debug_memory.html')

@app.route('/api/start_crawl', methods=['POST'])
@login_required
def start_crawl():
    from src.auth_db import get_crawls_last_24h, log_crawl_start

    data = request.get_json()
    url = data.get('url')

    if not url:
        return jsonify({'success': False, 'error': 'URL is required'})

    user_id = session.get('user_id')
    tier = session.get('tier', 'guest')

    # Check guest limits (IP-based) - skip in local mode
    if tier == 'guest' and not LOCAL_MODE:
        client_ip = get_client_ip()
        crawls_from_ip = get_guest_crawls_last_24h(client_ip)

        if crawls_from_ip >= 3:
            return jsonify({
                'success': False,
                'error': 'Guest limit reached: 3 crawls per 24 hours from your IP address. Please register for unlimited crawls.'
            })

        # Log this guest crawl
        log_guest_crawl(client_ip)

    # Get or create crawler for this session (this also ensures the session
    # has a session_id — reading it earlier would return None on a session
    # whose first API call is start_crawl, silently disabling persistence)
    crawler = get_or_create_crawler()
    settings_manager = get_session_settings()
    session_id = session.get('session_id')

    # Apply current settings to crawler before starting
    try:
        crawler_config = settings_manager.get_crawler_config()
        crawler.update_config(crawler_config)
    except Exception as e:
        print(f"Warning: Could not apply settings: {e}")

    # Enforce demo mode limits
    if DEMO_MODE:
        crawler.config['demo_mode'] = True
        crawler.config['demo_memory_limit_bytes'] = int(1.5 * 1024 * 1024 * 1024)  # 1.5GB

    # Pass user_id and session_id for database persistence
    success, message = crawler.start_crawl(url, user_id=user_id, session_id=session_id)

    # Store crawl_id in session
    if success and crawler.crawl_id:
        session['current_crawl_id'] = crawler.crawl_id
        # Also log to old crawl_history for compatibility
        log_crawl_start(user_id, url)

    return jsonify({'success': success, 'message': message, 'crawl_id': crawler.crawl_id})

@app.route('/api/stop_crawl', methods=['POST'])
@login_required
def stop_crawl():
    crawler = get_or_create_crawler()
    success, message = crawler.stop_crawl()
    return jsonify({'success': success, 'message': message})

def crawl_progress(crawl_id):
    """Progress for any crawl by id: status, pages done, total and an ETA."""
    from src.crawl_db import get_crawl_by_id

    crawl = get_crawl_by_id(crawl_id)
    if not crawl:
        return {'success': False, 'error': 'Crawl not found'}
    user_id = session.get('user_id')
    if user_id and crawl.get('user_id') not in (None, user_id):
        return {'success': False, 'error': 'Unauthorized'}

    done = crawl.get('urls_crawled') or 0
    total = max(crawl.get('urls_discovered') or 0, done)
    eta_seconds = None
    started, last_seen = crawl.get('started_at'), crawl.get('last_saved_at')
    if crawl.get('status') == 'running' and done and total > done and started and last_seen:
        try:
            elapsed = (datetime.fromisoformat(str(last_seen))
                       - datetime.fromisoformat(str(started))).total_seconds()
            if elapsed > 0:
                eta_seconds = round((total - done) / (done / elapsed))
        except (TypeError, ValueError):
            eta_seconds = None

    return {
        'success': True,
        'crawl_id': crawl_id,
        'status': crawl.get('status'),
        'url': crawl.get('base_url'),
        'pages_done': done,
        'pages_total': total,
        'progress': round((done / total) * 100, 1) if total else 0.0,
        'eta_seconds': eta_seconds,
        'started_at': crawl.get('started_at'),
        'completed_at': crawl.get('completed_at'),
    }


@app.route('/api/crawls', methods=['POST'])
@login_required
def create_crawl_endpoint():
    """Start a crawl and return its id.

    The same thing the UI's Start button does, named the way a script expects,
    and accepting the per-crawl options the feature request asks for.
    """
    payload = request.get_json(silent=True) or {}
    url = (payload.get('url') or '').strip()
    if not url:
        return jsonify({'success': False, 'error': 'A url is required'}), 400

    settings_manager = get_session_settings()
    overrides = {}
    if 'render' in payload:
        overrides['enable_javascript'] = bool(payload['render'])
        overrides['rendered_checks'] = bool(payload.get('rendered_checks', payload['render']))
    if payload.get('rules_file'):
        overrides['rules_file'] = payload['rules_file']
    if 'cms_connector' in payload:
        overrides['audit_cms'] = bool(payload['cms_connector'])
    for key in ('max_urls', 'max_depth', 'delay'):
        if key in payload:
            overrides[key] = payload[key]

    crawler = get_or_create_crawler()
    try:
        config = settings_manager.get_crawler_config()
        config.update(overrides)
        crawler.update_config(config)
    except Exception as e:
        print(f"Could not apply the crawl's settings: {e}")

    ok, message = crawler.start_crawl(url, user_id=session.get('user_id'),
                                      session_id=session.get('session_id'))
    if not ok:
        return jsonify({'success': False, 'error': message}), 400

    session['current_crawl_id'] = crawler.crawl_id
    return jsonify({'success': True, 'crawl_id': crawler.crawl_id,
                    'status_url': f'/api/crawl_status?crawl_id={crawler.crawl_id}'}), 201


@app.route('/api/crawl_status')
@login_required
def crawl_status():
    # A crawl can be asked about by id, which is what a CI job or another agent
    # has: it does not share this browser session, so the in-memory crawler is
    # not its crawl.
    wanted_id = request.args.get('crawl_id', type=int)
    if wanted_id:
        return jsonify(crawl_progress(wanted_id))

    crawler = get_or_create_crawler()
    settings_manager = get_session_settings()

    # Lightweight probe: status + stats only, no data (export pre-checks etc.)
    if request.args.get('stats_only'):
        status_data = crawler.get_status_light()
        if crawler.base_url:
            status_data['stats']['baseUrl'] = crawler.base_url
        return jsonify(status_data)

    since_seq = request.args.get('since_seq', type=int)

    if since_seq is None:
        # Full snapshot (initial page load, save-crawl fetch)
        status_data = crawler.get_status()
        if crawler.base_url:
            status_data['stats']['baseUrl'] = crawler.base_url

        exclusion_patterns = get_exclusion_patterns(settings_manager)
        status_data['issues'] = filter_issues_by_exclusion_patterns(
            status_data.get('issues', []), exclusion_patterns)

        return jsonify(status_data)

    # Event mode: everything that changed after since_seq. If the client's
    # epoch is stale (new crawl / loaded crawl / resume), reset=True and the
    # events replay from zero so the client rebuilds its state.
    #
    # Events are read before the stats snapshot on purpose: a crawl running
    # between the two calls should make the counters look newer than the rows,
    # never older, so the UI never shows fewer crawled URLs than it lists.
    epoch = request.args.get('epoch', '')
    reset, events, latest_seq, current_epoch = crawler.event_log.events_since(since_seq, epoch)

    status_data = crawler.get_status_light()
    if crawler.base_url:
        status_data['stats']['baseUrl'] = crawler.base_url

    exclusion_patterns = get_exclusion_patterns(settings_manager)
    if exclusion_patterns:
        events = [ev for ev in events
                  if ev['kind'] != 'issue' or not issue_matches_exclusion(ev['data'], exclusion_patterns)]

    status_data.update({
        'reset': reset,
        'epoch': current_epoch,
        'latest_seq': latest_seq,
        'events': events
    })
    return jsonify(status_data)

@app.route('/api/filter_issues', methods=['POST'])
@login_required
def filter_issues():
    try:
        data = request.get_json()
        issues = data.get('issues', [])
        settings_manager = get_session_settings()

        # Get current exclusion patterns
        current_settings = settings_manager.get_settings()
        exclusion_patterns_text = current_settings.get('issueExclusionPatterns', '')
        exclusion_patterns = [p.strip() for p in exclusion_patterns_text.split('\n') if p.strip()]

        # Filter issues
        filtered_issues = filter_issues_by_exclusion_patterns(issues, exclusion_patterns)

        return jsonify({'success': True, 'issues': filtered_issues})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def recover_crashed_crawls():
    """Check for and recover any crashed crawls on startup"""
    try:
        from src.crawl_db import get_crashed_crawls, set_crawl_status

        crashed = get_crashed_crawls()

        if crashed:
            print("\n" + "=" * 60)
            print("CRASH RECOVERY")
            print("=" * 60)
            for crawl in crashed:
                set_crawl_status(crawl['id'], 'failed')
                print(f"Found crashed crawl: {crawl['base_url']} (ID: {crawl['id']})")
                print(f"  → Marked as failed. User can resume from dashboard.")
            print("=" * 60 + "\n")
    except Exception as e:
        print(f"Error during crash recovery: {e}")

def graceful_shutdown(signum, frame):
    """Save all active crawls before shutdown"""
    print("\n" + "=" * 60)
    print("GRACEFUL SHUTDOWN")
    print("=" * 60)
    print("Saving all active crawls...")

    try:
        with instances_lock:
            for session_id, instance_data in list(crawler_instances.items()):
                crawler = instance_data['crawler']
                if crawler.is_running and crawler.crawl_id and crawler.db_save_enabled:
                    print(f"  → Saving crawl {crawler.crawl_id}...")
                    try:
                        crawler._save_batch_to_db(force=True)
                        crawler._save_queue_checkpoint()
                        from src.crawl_db import set_crawl_status
                        set_crawl_status(crawler.crawl_id, 'paused')
                    except Exception as e:
                        print(f"    Error saving crawl {crawler.crawl_id}: {e}")

        print("All crawls saved successfully")
        print("=" * 60)
    except Exception as e:
        print(f"Error during shutdown: {e}")

    print("Goodbye!")
    import sys
    sys.exit(0)

def main():
    import signal

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    # Recover any crashed crawls from previous session
    recover_crashed_crawls()

    # Start cleanup thread for old crawler instances
    start_cleanup_thread()

    print("=" * 60)
    print("Toadstone - SEO Spider")
    print("=" * 60)
    print(f"\n🚀 Server starting on http://0.0.0.0:{PORT}")
    print(f"🌐 Access from browser: http://localhost:{PORT}")
    print(f"📱 Access from network: http://<your-ip>:{PORT}")
    print(f"\n✨ Multi-tenancy enabled - each browser session is isolated")
    print(f"💾 Settings stored in browser localStorage")
    print(f"\nPress Ctrl+C to stop the server\n")
    print("=" * 60 + "\n")

    # Open browser in a separate thread after short delay
    def open_browser():
        time.sleep(1.5)  # Wait for Flask to start
        webbrowser.open(f'http://localhost:{PORT}')

    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()

    # The scheduler only runs in the server process, never in a CLI crawl:
    # a one-shot command should not quietly start firing standing jobs.
    _start_scheduler()

    # Run Flask server with Waitress (production-grade WSGI server)
    from waitress import serve
    print(f"Starting Toadstone on http://localhost:{PORT}")
    print("Using Waitress WSGI server with multi-threading support")
    serve(app, host='0.0.0.0', port=PORT, threads=8)

def run_cli_crawl(url, fail_on=None, report_path=None):
    """Crawl once, print a summary and exit non-zero when it fails the gate.

    This is the CI entry point: `python main.py --crawl https://site --fail-on error`
    returns 1 when the crawl finds an open issue at that severity or worse, so a
    pipeline can block a deploy on it.
    """
    from src.crawler import WebCrawler

    crawler = WebCrawler()
    try:
        crawler.update_config(SettingsManager().get_crawler_config())
    except Exception as e:
        print(f"Using default settings ({e})")

    ok, message = crawler.start_crawl(url)
    if not ok:
        print(f"Could not start the crawl: {message}")
        return 2

    while crawler.is_running:
        time.sleep(0.5)

    issues = crawler.issue_detector.get_issues() if crawler.issue_detector else []
    counts = {'error': 0, 'warning': 0, 'info': 0}
    for issue in issues:
        counts[issue.get('type', 'info')] = counts.get(issue.get('type', 'info'), 0) + 1

    print(f"\nCrawled {crawler.stats['crawled']} URLs of {url}")
    print(f"  {counts['error']} errors, {counts['warning']} warnings, {counts['info']} info")

    if report_path:
        with open(report_path, 'w') as handle:
            json.dump({'url': url, 'crawled': crawler.stats['crawled'],
                       'counts': counts, 'issues': issues}, handle, indent=2, default=str)
        print(f"  report written to {report_path}")

    if not fail_on:
        return 0

    ranked = ['error', 'warning', 'info']
    blocking = ranked[:ranked.index(fail_on) + 1]
    failing = sum(counts.get(level, 0) for level in blocking)
    if failing:
        print(f"\nFAILED: {failing} issue(s) at severity '{fail_on}' or worse")
        for issue in issues:
            if issue.get('type') in blocking:
                print(f"  [{issue['type']}] {issue.get('issue')} - {issue.get('url')}")
        return 1

    print(f"\nPASSED: no issues at severity '{fail_on}' or worse")
    return 0


if __name__ == '__main__':
    if args.crawl:
        sys.exit(run_cli_crawl(args.crawl, args.fail_on, args.report))
    main()
