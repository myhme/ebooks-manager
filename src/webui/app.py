from flask import Flask, render_template, jsonify, request
from pathlib import Path
import logging
from datetime import datetime
import json
from src.utils.config_loader import load_config
from src.sync_logic import orchestrate_sync
from src.scrapers.goodreads import GoodreadsScraper

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)

@app.template_filter('strftime')
def _jinja2_filter_datetime(date_str, fmt='%Y-%m-%d %H:%M:%S'):
    if not date_str:
        return "N/A"
    try:
        if isinstance(date_str, str):
            dt = datetime.fromisoformat(date_str)
        elif isinstance(date_str, datetime):
            dt = date_str
        else:
            return date_str
        return dt.strftime(fmt)
    except (ValueError, TypeError):
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
        except json.JSONDecodeError:
            logging.warning("History file is malformed or empty.")
        except Exception as e:
            logging.error(f"Error reading history file: {e}", exc_info=True)
    try:
        if log_path.exists():
            with open(log_path, 'r') as f:
                lines = f.readlines()
                log_content = "".join(lines[-50:])
    except Exception as e:
        logging.error(f"Error reading log file: {e}", exc_info=True)
    return render_template('status.html', history=history, log_content=log_content, now=datetime.now())

@app.route('/history')
def history():
    return status()

@app.route('/sync', methods=['GET', 'POST'])
def sync():
    if request.method == 'POST':
        try:
            orchestrate_sync()
            return jsonify({'status': 'success', 'message': 'Sync triggered successfully'})
        except Exception as e:
            logging.error(f"Sync failed: {e}", exc_info=True)
            return jsonify({'status': 'error', 'message': str(e)}), 500
    return render_template('sync.html')

@app.route('/shelf/<shelf_name>')
def shelf_view(shelf_name):
    config = load_config()
    scraper = GoodreadsScraper(config)
    books = scraper.get_goodreads_books_from_shelf(shelf_name)
    sync_status = "Last sync: N/A"  # TODO: Pull from history or DB
    return render_template('shelf_view.html', books=books, shelf_name=shelf_name, sync_status=sync_status)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5002, debug=False)
