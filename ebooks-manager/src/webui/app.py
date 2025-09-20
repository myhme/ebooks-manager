from flask import Flask, render_template, jsonify, request, url_for
from pathlib import Path
import logging
from datetime import datetime
import json
from threading import Thread, Lock
from src.utils.config_loader import load_config
from src.sync_logic import orchestrate_sync
from src.scrapers.goodreads import GoodreadsScraper

app = Flask(__name__, template_folder='templates')
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Global sync state
sync_lock = Lock()
sync_state = {
    'running': False,
    'last_started': None,
    'last_finished': None,
    'last_result': None
}

def run_sync_background():
    with sync_lock:
        sync_state['running'] = True
        sync_state['last_started'] = datetime.now().isoformat()
    try:
        orchestrate_sync()
        with sync_lock:
            sync_state['last_result'] = 'success'
    except Exception as e:
        logger.exception("Background sync failed: %s", e)
        with sync_lock:
            sync_state['last_result'] = 'error'
    finally:
        with sync_lock:
            sync_state['running'] = False
            sync_state['last_finished'] = datetime.now().isoformat()

@app.template_filter('strftime')
def _jinja2_filter_datetime(date_str, fmt='%Y-%m-%d %H:%M:%S'):
    if not date_str:
        return "N/A"
    try:
        if isinstance(date_str, str):
            dt = datetime.fromisoformat(date_str)
        else:
            dt = date_str
        return dt.strftime(fmt)
    except Exception:
        return date_str

@app.route('/')
@app.route('/status')
def status():
    history_path = Path('/app/logs/history.json')
    history = []
    log_path = Path('/app/logs/sync_log.txt')
    log_content = ""
    if history_path.exists():
        try:
            with open(history_path, 'r') as f:
                content = f.read()
                if content:
                    history = json.loads(content)
                    history.reverse()
        except Exception:
            logger.warning("History file malformed or unreadable")
    try:
        if log_path.exists():
            with open(log_path, 'r') as f:
                log_content = "".join(f.readlines()[-50:])
    except Exception:
        logger.exception("Error reading log file")
    return render_template('status.html', history=history, log_content=log_content, now=datetime.now(), sync_state=sync_state)

@app.route('/sync', methods=['GET','POST'])
def sync():
    # If POST -> trigger background sync and return immediately
    if request.method == 'POST':
        if sync_state['running']:
            return jsonify({'status':'running','message':'Sync already running'}), 202
        # spawn background thread
        thread = Thread(target=run_sync_background, daemon=True)
        thread.start()
        return jsonify({'status':'started','message':'Sync started in background'}), 202
    return render_template('sync.html', sync_state=sync_state)

@app.route('/sync/status')
def sync_status():
    return jsonify(sync_state)

@app.route('/shelf/<shelf_name>')
def shelf_view(shelf_name):
    config = load_config()
    scraper = GoodreadsScraper(config)
    books = scraper.get_goodreads_books_from_shelf(shelf_name)
    sync_status = f"Running: {sync_state['running']}"
    return render_template('shelf_view.html', books=books, shelf_name=shelf_name, sync_status=sync_status)

if __name__ == "__main__":
    config = load_config()
    app.run(host='0.0.0.0', port=int(os.getenv('FLASK_PORT', 5002)), debug=False)
