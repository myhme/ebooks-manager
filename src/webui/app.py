# src/webui/app.py
import os
import json
import logging
import threading
import time
from functools import wraps
from pathlib import Path
from typing import Tuple, Union

import difflib
import requests
from flask import Flask, request, jsonify, render_template, send_from_directory, redirect, url_for, current_app
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.wrappers import Response
from werkzeug.security import check_password_hash

# project imports (adjust if your import paths differ)
try:
    from src.utils.database import Database
    from src.scrapers.goodreads import GoodreadsScraper
except Exception:
    from utils.database import Database
    from scrapers.goodreads import GoodreadsScraper

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = Flask(__name__, static_folder='static', template_folder='templates')
app.wsgi_app = ProxyFix(app.wsgi_app)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# load config from file if present
CONFIG_PATH = os.getenv('APP_CONFIG_JSON', 'config.json')
if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, 'r') as f:
            cfg = json.load(f)
            app.config.update(cfg)
            LOG.info("Loaded config from %s", CONFIG_PATH)
    except Exception:
        LOG.exception("Failed to load config.json")
else:
    LOG.info("No config.json found at %s; using env vars", CONFIG_PATH)

def get_config(key, default=None):
    v = app.config.get(key)
    if v is None:
        v = os.getenv(key.upper(), default)
    return v

# Database init
DB_PATH = get_config('database_path') or get_config('DATABASE_PATH') or '/app/data/databases/goodreads.db'
db = Database(DB_PATH)

# CWA backend config helper
def _get_cwa_config() -> Tuple[Union[str,None], Union[str,None], Union[str,None]]:
    base = app.config.get('cwa_api_url') or os.getenv('CWA_API_URL') or os.getenv('DOWNLOAD_BACKEND_URL') or app.config.get('download_backend_url')
    username = app.config.get('cwa_username') or os.getenv('CWA_USERNAME')
    password = app.config.get('cwa_password') or os.getenv('CWA_PASSWORD')
    if base and base.endswith('/'):
        base = base[:-1]
    return base, username, password

def _call_cwa_api(path: str, method: str = 'GET', params=None, json_body=None, timeout=12):
    base, username, password = _get_cwa_config()
    if not base:
        return False, 500, "CWA backend not configured (cwa_api_url)"
    url = f"{base}/{path.lstrip('/')}"
    auth = (username, password) if username and password else None
    try:
        r = requests.request(method, url, params=params, json=json_body, auth=auth, timeout=timeout)
    except requests.RequestException as e:
        LOG.error("Backend HTTP call failed %s %s", url, e)
        return False, 502, f"Backend unreachable: {repr(e)}"
    try:
        ct = r.headers.get('Content-Type','')
        if 'application/json' in ct or (r.text and (r.text.strip().startswith('{') or r.text.strip().startswith('['))):
            return True, r.status_code, r.json()
        else:
            return True, r.status_code, r.text
    except Exception:
        return True, r.status_code, r.text

# Authentication - reuse CWA calibre-web DB if configured
CWA_DB_PATH = get_config('CWA_DB_PATH') or get_config('cwa_db_path') or None
def authenticate() -> bool:
    if not CWA_DB_PATH:
        return True
    if not request.authorization:
        return False
    username = request.authorization.get('username')
    password = request.authorization.get('password')
    import sqlite3
    try:
        db_uri = f"file:{CWA_DB_PATH}?mode=ro&immutable=1"
        conn = sqlite3.connect(db_uri, uri=True)
        cur = conn.cursor()
        cur.execute("SELECT password FROM user WHERE name = ?", (username,))
        row = cur.fetchone()
        conn.close()
        if not row or not row[0] or not check_password_hash(row[0], password):
            LOG.warning("Auth failed for user %s", username)
            return False
    except Exception:
        LOG.exception("Auth check failed")
        return False
    LOG.info("Authentication successful for %s", username)
    return True

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not authenticate():
            return Response(response="Unauthorized", status=401, headers={"WWW-Authenticate": 'Basic realm="Login Required"'})
        return f(*args, **kwargs)
    return decorated

# Serve cover images saved by scraper
@app.route('/covers/<path:filename>')
@login_required
def serve_cover(filename):
    covers_dir = Path(get_config('cache_dir', '/app/data/cache')) / 'covers'
    if not covers_dir.exists():
        return "Not Found", 404
    return send_from_directory(str(covers_dir), filename)

# Basic pages (index, shelves, shelf view, downloads)
@app.route('/')
@login_required
def index():
    return redirect(url_for('shelf_view', shelf_name='to-download'))

@app.route('/shelves')
@login_required
def shelves_page():
    return render_template('shelves.html')

@app.route('/shelf/<shelf_name>')
@login_required
def shelf_view(shelf_name):
    try:
        per_page = int(request.args.get('per_page', get_config('webui_per_page', 30)))
    except Exception:
        per_page = 30
    try:
        page = int(request.args.get('page', 1))
    except Exception:
        page = 1
    offset = (page - 1) * per_page

    try:
        books = db.get_books_by_shelf(shelf_name, limit=per_page, offset=offset)
    except TypeError:
        LOG.warning("Database.get_books_by_shelf does not accept offset; calling without")
        books = db.get_books_by_shelf(shelf_name, limit=per_page)

    try:
        total = db.count_books_by_shelf(shelf_name)
    except Exception:
        total = len(books)
    total_pages = max(1, (int(total) + per_page - 1) // per_page)

    return render_template('shelf_view.html',
                           shelf_name=shelf_name,
                           books=books,
                           per_page=per_page,
                           page=page,
                           total_pages=total_pages,
                           offset=offset,
                           config=app.config)

@app.route('/downloads')
@login_required
def downloads_page():
    return render_template('downloads.html')

@app.route('/status')
@login_required
def status_page():
    return render_template('status.html')

@app.route('/sync')
@login_required
def sync_page():
    return render_template('sync.html')

# Existing proxy endpoints (status, active, download, cancel)
@app.route('/api/status_proxy', methods=['GET'])
@login_required
def api_status_proxy():
    ok, code, body = _call_cwa_api('/api/status', 'GET')
    if not ok:
        return jsonify({"error": body}), 502
    return jsonify(body), 200

@app.route('/api/active_proxy', methods=['GET'])
@login_required
def api_active_proxy():
    ok, code, body = _call_cwa_api('/api/downloads/active', 'GET')
    if not ok:
        return jsonify({"error": body}), 502
    return jsonify(body), 200

@app.route('/api/download_proxy', methods=['POST'])
@login_required
def api_download_proxy():
    payload = request.get_json(silent=True) or request.values.to_dict()
    if not payload:
        return jsonify({"error": "No payload"}), 400
    book_id = payload.get('id') or payload.get('book_id')
    if not book_id:
        return jsonify({"error": "No id provided"}), 400
    priority = payload.get('priority', 0)
    ok, code, body = _call_cwa_api('/api/download', 'GET', params={'id': book_id, 'priority': priority})
    if not ok:
        ok2, code2, body2 = _call_cwa_api('/api/download', 'POST', json_body={'id': book_id, 'priority': priority})
        if not ok2:
            return jsonify({"error": body2}), 502
        return jsonify(body2), code2
    return jsonify(body), code

@app.route('/api/download/<book_id>/cancel_proxy', methods=['DELETE'])
@login_required
def api_cancel_download_proxy(book_id):
    ok, code, body = _call_cwa_api(f'/api/download/{book_id}/cancel', 'DELETE')
    if ok and 200 <= code < 300:
        return jsonify(body if isinstance(body, dict) else {"status":"cancelled","book_id":book_id}), code
    # try alternate path
    ok2, code2, body2 = _call_cwa_api(f'/api/downloads/{book_id}/cancel', 'DELETE')
    if ok2 and 200 <= code2 < 300:
        return jsonify(body2), code2
    return jsonify({"error":"Failed to cancel; backend unreachable or endpoint not supported"}), 502

# Helper: string normalization
def _normalize_str(s: str):
    if not s:
        return ''
    s = s.lower()
    s = re_clean = ''.join(ch for ch in s if ch.isalnum() or ch.isspace()).strip()
    return re_clean

# New: search+save endpoint + auto-queue best match
@app.route('/api/search_and_queue', methods=['POST'])
@login_required
def api_search_and_queue():
    """
    Accepts JSON:
      { goodreads_id, title, author, isbn, isbn13 }
    Calls CWA backend /api/search (GET) with sensible params,
    saves search request + response JSON to cache_dir/search_requests/,
    scores candidates, auto-queues if confident, and returns JSON
    { queued: true, candidate: {...} } or { candidates: [...] }.
    """
    payload = request.get_json(silent=True) or {}
    if not payload:
        return jsonify({"error":"No payload"}), 400

    title = payload.get('title') or ''
    author = payload.get('author') or ''
    isbn = payload.get('isbn') or payload.get('isbn13') or ''
    goodreads_id = str(payload.get('goodreads_id') or '')

    # build query param - prefer isbn if present else "title author"
    query = isbn if isbn else (title + ' ' + author).strip()
    params = {'query': query}
    if title:
        params['title'] = title
    if author:
        params['author'] = author
    if isbn:
        params['isbn'] = isbn

    ok, status_code, resp = _call_cwa_api('/api/search', 'GET', params=params)
    timestamp = int(time.time())
    cache_dir = Path(get_config('cache_dir', '/app/data/cache'))
    search_dir = cache_dir / 'search_requests'
    search_dir.mkdir(parents=True, exist_ok=True)
    saved = {
        'timestamp': timestamp,
        'goodreads_id': goodreads_id,
        'query': params,
        'backend_response': resp if ok else {"error": resp}
    }
    fname = search_dir / f"{timestamp}_{goodreads_id or 'noid'}.json"
    try:
        with open(fname, 'w', encoding='utf-8') as f:
            json.dump(saved, f, ensure_ascii=False, indent=2)
    except Exception:
        LOG.exception("Failed to save search request JSON")

    if not ok:
        return jsonify({"error": "Backend search failed", "detail": resp}), 502

    # resp hopefully is list of candidates (books)
    candidates = resp if isinstance(resp, list) else (resp.get('books') if isinstance(resp, dict) else None)
    if not candidates:
        # if response is non-list, try to return as-is
        return jsonify({"error":"No candidates", "raw": resp}), 200

    # score candidates
    def score_candidate(cand):
        # cand expected to have .title and .author and maybe isbn / id
        s = 0
        ct = cand.get('title') or cand.get('Title') or ''
        ca = cand.get('author') or cand.get('Author') or ''
        c_isbns = []
        for k in ('isbn','isbn13','ISBN','ISBN13'):
            v = cand.get(k)
            if v:
                if isinstance(v, list):
                    c_isbns.extend(v)
                else:
                    c_isbns.append(str(v))
        # ISBN exact match high boost
        if isbn and any(isbn.replace('-', '') == c.replace('-', '') for c in c_isbns):
            s += 2000
        # exact title match
        if title and ct and _normalize_str(title) == _normalize_str(ct):
            s += 1000
        # exact author contains
        if author and ca and _normalize_str(author) in _normalize_str(ca):
            s += 400
        # fuzzy title ratio
        try:
            r = difflib.SequenceMatcher(None, _normalize_str(title), _normalize_str(ct)).ratio() if title and ct else 0.0
            s += int(r * 300)
        except Exception:
            pass
        # fuzzy author ratio
        try:
            r2 = difflib.SequenceMatcher(None, _normalize_str(author), _normalize_str(ca)).ratio() if author and ca else 0.0
            s += int(r2 * 150)
        except Exception:
            pass
        # slight preference for common ebook formats if present
        fmt = (cand.get('format') or '').lower()
        if fmt in ('epub','mobi','azw3','pdf'):
            s += 20
        return s

    scored = []
    for c in candidates:
        try:
            sc = score_candidate(c)
            scored.append((sc, c))
        except Exception:
            LOG.exception("Scoring candidate failed")
    scored.sort(key=lambda x: x[0], reverse=True)

    # threshold to auto-queue
    best_score, best_candidate = scored[0]
    LOG.info("Top candidate score=%s for gr=%s", best_score, goodreads_id)
    # Save scored candidate list for auditing
    cand_dir = cache_dir / 'search_candidates'
    cand_dir.mkdir(parents=True, exist_ok=True)
    try:
        with open(cand_dir / f"{timestamp}_{goodreads_id or 'noid'}.json", 'w', encoding='utf-8') as f:
            json.dump({'scored': [{'score': s, 'candidate': c} for s, c in scored]}, f, ensure_ascii=False, indent=2)
    except Exception:
        LOG.exception("Failed to save candidates JSON")

    # If confident (score big) auto queue
    AUTO_THRESHOLD = app.config.get('auto_queue_score_threshold') or 600
    if best_score >= int(AUTO_THRESHOLD):
        # attempt to queue by calling download
        cand_id = best_candidate.get('id') or best_candidate.get('md5') or best_candidate.get('ID') or best_candidate.get('Id')
        if not cand_id:
            return jsonify({"error":"No backend id for top candidate", "candidate": best_candidate}), 200
        ok2, code2, body2 = _call_cwa_api('/api/download', 'GET', params={'id': cand_id, 'priority': 0})
        if not ok2:
            ok3, code3, body3 = _call_cwa_api('/api/download', 'POST', json_body={'id': cand_id, 'priority': 0})
            if not ok3:
                return jsonify({"error":"Failed to queue candidate", "detail": body3}), 502
            queued_info = body3
        else:
            queued_info = body2
        # save selection
        sel_dir = cache_dir / 'selected_candidates'
        sel_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(sel_dir / f"{timestamp}_{goodreads_id or 'noid'}.json", 'w', encoding='utf-8') as f:
                json.dump({'selected': best_candidate, 'queued_result': queued_info}, f, ensure_ascii=False, indent=2)
        except Exception:
            LOG.exception("Failed to save selected candidate JSON")
        return jsonify({"queued": True, "candidate": best_candidate, "queued_result": queued_info}), 200

    # not confident: return top N candidates for manual selection
    topN = [c for s, c in scored[:6]]
    return jsonify({"queued": False, "candidates": topN, "reason": "low_confidence", "top_score": best_score}), 200


@app.route('/search')
@login_required
def manual_search_page():
    return render_template('search.html')

@app.route('/api/manual_search', methods=['POST'])
@login_required
def api_manual_search():
    payload = request.get_json(silent=True) or {}
    query = payload.get('query')
    if not query:
        return jsonify({"error":"query required"}), 400

    # throttle: avoid spamming backend
    time.sleep(1.0)  # simple delay, could use token-bucket limiter

    # retry up to 3 times
    last_err = None
    for attempt in range(3):
        ok, code, resp = _call_cwa_api('/api/search', 'GET', params={'query': query})
        if ok and code == 200:
            candidates = resp if isinstance(resp, list) else resp.get('books', [])
            return jsonify({"candidates": candidates})
        last_err = resp
        time.sleep(2**attempt)  # exponential backoff
    return jsonify({"error":"backend search failed","detail":last_err}), 502



# queue from candidate endpoint (manual choose)
@app.route('/api/queue_from_candidate', methods=['POST'])
@login_required
def api_queue_from_candidate():
    payload = request.get_json(silent=True) or {}
    cand_id = payload.get('candidate_id') or payload.get('id')
    if not cand_id:
        return jsonify({"error":"candidate_id is required"}), 400
    ok, code, body = _call_cwa_api('/api/download', 'GET', params={'id': cand_id, 'priority': payload.get('priority', 0)})
    if not ok:
        ok2, code2, body2 = _call_cwa_api('/api/download', 'POST', json_body={'id': cand_id, 'priority': payload.get('priority', 0)})
        if not ok2:
            return jsonify({"error": body2}), 502
        return jsonify(body2), code2
    return jsonify(body), code

# spawn background sync (same as earlier)
def _spawn_background_sync(shelf_name: str):
    def run():
        try:
            cfg = app.config.copy()
            cfg['cache_dir'] = cfg.get('cache_dir') or get_config('cache_dir', '/app/data/cache')
            cfg['goodreads_per_page'] = cfg.get('goodreads_per_page') or get_config('goodreads_per_page', get_config('GOODREADS_PER_PAGE', 100))
            gs = GoodreadsScraper(cfg)
            gs.get_goodreads_books_from_shelf(shelf_name=shelf_name, fetch_details=False, max_pages=200)
            LOG.info("Background sync finished for shelf %s", shelf_name)
        except Exception:
            LOG.exception("Background sync failed")
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t

@app.route('/sync/<shelf_name>', methods=['POST'])
@login_required
def trigger_sync(shelf_name):
    _spawn_background_sync(shelf_name)
    return jsonify({"status":"sync_started", "shelf": shelf_name})

# small endpoint to test backend reachability
@app.route('/api/backend_info')
@login_required
def backend_info():
    base, user, pw = _get_cwa_config()
    ok, code, body = _call_cwa_api('/api/status', 'GET')
    if not ok:
        return jsonify({"ok": False, "error": body, "base": base}), 200
    return jsonify({"ok": True, "backend_status": body, "base": base}), 200

if __name__ == '__main__':
    LOG.info("Starting webui on 0.0.0.0:5002")
    app.run(host='0.0.0.0', port=int(os.getenv('WEBUI_PORT', 5002)), debug=False)
