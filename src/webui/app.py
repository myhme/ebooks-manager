from flask import Flask, render_template, jsonify
import json
import os
import logging
import google.generativeai as genai
from datetime import datetime
import sys
import threading

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from job_runner import sync_job

app = Flask(__name__)
LOG_DIR, LOG_FILE, HISTORY_FILE, CONFIG_FILE = '/app/logs', '/app/logs/sync_log.txt', '/app/logs/history.json', '/app/config/config.json'

handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)

def load_config():
    try:
        with open(CONFIG_FILE) as f: return json.load(f)
    except Exception: return {}

def get_recent_reads_from_history():
    if not os.path.exists(HISTORY_FILE): return []
    with open(HISTORY_FILE, 'r') as f:
        try: history = json.load(f)
        except json.JSONDecodeError: return []
    recent_books = [book for entry in history if entry.get('status') == 'Success' for book in entry.get('books_processed', [])]
    return list(dict.fromkeys(recent_books))[:10]

@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/status')
def api_status():
    log_content = "Log file not found."
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r') as f: log_content = "".join(f.readlines()[-100:])
    return jsonify({'log_content': log_content, 'last_checked': datetime.now().isoformat()})

@app.route('/api/history')
def api_history():
    if not os.path.exists(HISTORY_FILE): return jsonify([])
    with open(HISTORY_FILE, 'r') as f:
        try: return jsonify(json.load(f))
        except json.JSONDecodeError: return jsonify([{'status': 'Error', 'error': 'History file is corrupt.'}])

@app.route('/api/sync-now', methods=['POST'])
def api_sync_now():
    app.logger.info("Manual sync triggered via API.")
    threading.Thread(target=sync_job).start()
    return jsonify({'message': 'Sync process started in the background.'}), 202

@app.route('/api/recommendations', methods=['GET'])
def api_recommendations():
    config = load_config()
    api_key = config.get('gemini_api_key')
    if not api_key or "YOUR_GOOGLE_GEMINI_API_KEY" in api_key:
        return jsonify({'error': 'Gemini API key is not configured.'}), 400
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.0-pro')
        recent_books = get_recent_reads_from_history()
        if not recent_books: return jsonify({'recommendations': "Not enough reading history for recommendations."})
        
        prompt = f"""Based on my recently read books ({", ".join(f'"{b}"' for b in recent_books)}), recommend 5 more books. For each, provide title, author, and a one-sentence reason. Format as a valid JSON array of objects with "title", "author", "reason" keys. No text outside the JSON array."""
        response = model.generate_content(prompt)
        recommendations = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
        return jsonify({'recommendations': recommendations})
    except Exception as e:
        app.logger.error(f"Error getting recommendations: {e}")
        return jsonify({'error': f'Error with Gemini API: {e}'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=False)
