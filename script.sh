#!/bin/bash

# ==============================================================================
# Ebooks Manager Setup Script (v6)
# ==============================================================================
# This script creates the necessary directory structure and files for the
# ebooks-manager project. It sets up a single, all-in-one Docker container
# that runs the sync service, web UI, and a headless browser.
#
# Changes in v6:
# - Made the scheduler in `main.py` more resilient. It no longer exits if the
#   config file is invalid on startup. Instead, it logs a clear warning and
#   waits for the config to be fixed, allowing the web UI to remain fully
#   operational.
# ==============================================================================

echo "🚀 Starting Ebooks Manager setup (v6 - Startup Fix)..."

# 1. Create Directory Structure
# ------------------------------------------------------------------------------
echo "📁 Creating project directories..."
mkdir -p config src/webui/templates logs

touch src/__init__.py
touch src/webui/__init__.py

echo "✅ Directories created successfully."

# 2. Create Configuration Template
# ------------------------------------------------------------------------------
echo "📝 Creating configuration template..."
cat << 'EOF' > config/config.json.template
{
    "goodreads_user_id": "1234",
    "storygraph_email": "abc@g.com",
    "storygraph_password": "password",
    "gemini_api_key": "apikey",
    "sync_interval_hours": 6
}
EOF
echo "✅ Configuration template created. Please edit 'config/config.json' with your details."

# 3. Create Simplified Docker Compose File
# ------------------------------------------------------------------------------
echo "🐳 Creating simplified docker-compose.yml..."
cat << 'EOF' > docker-compose.yml
services:
  app:
    build: .
    container_name: ebooks-manager-app
    ports:
      - "${WEBUI_PORT:-5002}:5002"
    volumes:
      - ./config:/app/config
      - ./logs:/app/logs
    shm_size: '2g' # Allocate shared memory for Chrome
    restart: unless-stopped
EOF
echo "✅ docker-compose.yml simplified for a single service."

# 4. Create .env file for port configuration
# ------------------------------------------------------------------------------
echo "🔑 Creating .env file for configuration..."
cat << 'EOF' > .env
# --- Ebooks Manager Environment Variables ---
# Change the port the web UI is accessible on.
# e.g., to access the UI at http://localhost:8080, set WEBUI_PORT=8080
WEBUI_PORT=5002
EOF
echo "✅ .env file created."

# 5. Create the All-in-One Dockerfile
# ------------------------------------------------------------------------------
echo "📦 Creating All-in-One Dockerfile..."
cat << 'EOF' > Dockerfile
# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# --- Install Dependencies ---
# Install essential tools plus 'jq' for parsing JSON from web APIs.
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    unzip \
    ca-certificates \
    jq \
    # Add dependencies for Chrome
    libglib2.0-0 libnss3 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

# --- Install Latest Stable Google Chrome and ChromeDriver ---
# This block dynamically finds and downloads the latest stable versions to prevent build failures.
RUN CHROME_VERSION_URL="https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json" && \
    CHROME_URL=$(wget -q -O - "$CHROME_VERSION_URL" | jq -r '.channels.Stable.downloads.chrome[] | select(.platform=="linux64") | .url') && \
    CHROMEDRIVER_URL=$(wget -q -O - "$CHROME_VERSION_URL" | jq -r '.channels.Stable.downloads.chromedriver[] | select(.platform=="linux64") | .url') && \
    \
    # Download and install Google Chrome by unzipping it
    wget --no-verbose -O /tmp/chrome.zip "$CHROME_URL" && \
    unzip /tmp/chrome.zip -d /opt && \
    ln -s /opt/chrome-linux64/chrome /usr/bin/google-chrome && \
    rm /tmp/chrome.zip && \
    \
    # Download and install ChromeDriver
    wget --no-verbose -O /tmp/chromedriver.zip "$CHROMEDRIVER_URL" && \
    unzip /tmp/chromedriver.zip -d /usr/local/bin/ && \
    mv /usr/local/bin/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver && \
    chmod +x /usr/local/bin/chromedriver && \
    rm -rf /usr/local/bin/chromedriver-linux64 && \
    rm /tmp/chromedriver.zip

# --- Install Python Dependencies ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Copy Application Code and Entrypoint ---
COPY src/ .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# The command to run when the container starts
ENTRYPOINT ["./entrypoint.sh"]
EOF
echo "✅ All-in-One Dockerfile created with dynamic version fetching."

# 6. Create the entrypoint script
# ------------------------------------------------------------------------------
echo "🚀 Creating entrypoint.sh..."
cat << 'EOF' > entrypoint.sh
#!/bin/bash

# Start the background sync scheduler
echo "Starting background scheduler..."
python /app/main.py &

# Start the Flask Web UI in the foreground
echo "Starting Web UI..."
exec python /app/webui/app.py
EOF
echo "✅ entrypoint.sh created."


# 7. Create requirements.txt
# ------------------------------------------------------------------------------
echo "📋 Creating requirements.txt..."
cat << 'EOF' > requirements.txt
requests
beautifulsoup4
lxml
selenium==4.21.0
schedule
flask
google-generativeai
python-dotenv
EOF
echo "✅ requirements.txt created."

# 8. Create the core job runner script (No changes from v5)
# ------------------------------------------------------------------------------
echo "🏃 Creating src/job_runner.py..."
cat << 'EOF' > src/job_runner.py
import logging
import json
import os
from sync_logic import BookSyncAutomation
from datetime import datetime

# --- Configuration and Paths ---
LOG_DIR = 'logs'
LOG_FILE = os.path.join(LOG_DIR, 'sync_log.txt')
HISTORY_FILE = os.path.join(LOG_DIR, 'history.json')
CONFIG_FILE = 'config/config.json'

# --- Ensure log directory exists ---
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# --- Logger Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

def load_config():
    """Loads configuration from the JSON file."""
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        required_keys = ['goodreads_user_id', 'storygraph_email', 'storygraph_password']
        if not all(key in config and config[key] and "YOUR_" not in config[key] for key in required_keys):
            raise ValueError("One or more required keys are missing or not set in config.json")
        return config
    except FileNotFoundError:
        logging.error(f"FATAL: Config file not found at {CONFIG_FILE}. Please create it from the template.")
        raise
    except (json.JSONDecodeError, ValueError) as e:
        logging.error(f"FATAL: Error in config file: {e}")
        raise

def update_history(status, books_processed, error_message=None):
    """Updates the task history log."""
    history_entry = {
        'timestamp': datetime.now().isoformat(),
        'status': status,
        'books_processed': books_processed,
        'error': error_message
    }
    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r') as f:
            try:
                history = json.load(f)
            except json.JSONDecodeError:
                history = []
    history.insert(0, history_entry)
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history[:50], f, indent=4)


def sync_job():
    """The main job function. Can be called by scheduler or API."""
    logging.info("="*50)
    logging.info("🚀 Starting Goodreads to StoryGraph sync job...")
    logging.info("="*50)
    
    sync_bot = None
    processed_titles = []
    
    try:
        config = load_config()
        sync_bot = BookSyncAutomation(
            goodreads_user_id=config['goodreads_user_id'],
            storygraph_email=config['storygraph_email'],
            storygraph_password=config['storygraph_password']
        )
        processed_titles = sync_bot.sync_books()
        logging.info("✅ Sync job completed successfully.")
        update_history("Success", processed_titles)

    except Exception as e:
        logging.error(f"❌ Sync job failed: {e}", exc_info=True)
        update_history("Failure", processed_titles, error_message=str(e))
    finally:
        if sync_bot and sync_bot.driver:
            logging.info("Browser is being closed by the sync_logic class.")
        logging.info("="*50)
        logging.info("Sync job finished.")
        logging.info("="*50)
EOF
echo "✅ src/job_runner.py created."

# 9. Create the main application script (Scheduler) (MODIFIED)
# ------------------------------------------------------------------------------
echo "🐍 Creating src/main.py..."
cat << 'EOF' > src/main.py
import schedule
import time
import logging
from job_runner import sync_job, load_config

if __name__ == "__main__":
    logging.info("Ebooks Manager Sync Service started.")
    
    sync_interval = 6
    config_valid = False

    # Try to load config, but don't exit if it fails.
    try:
        app_config = load_config()
        sync_interval = app_config.get('sync_interval_hours', 6)
        config_valid = True
        logging.info(f"Configuration loaded. Sync will run every {sync_interval} hours.")
    except Exception as e:
        logging.warning(f"Could not load config: {e}")
        logging.warning("Scheduler will run, but syncs will fail until config is corrected.")

    # Run the job once on startup ONLY if the config was valid
    if config_valid:
        logging.info("Running initial sync on startup...")
        sync_job()
    else:
        logging.warning("Skipping initial sync due to invalid configuration.")

    # Schedule the job regardless of config state.
    # The job itself will handle failing gracefully.
    schedule.every(sync_interval).hours.do(sync_job)

    while True:
        schedule.run_pending()
        time.sleep(60)
EOF
echo "✅ src/main.py updated to be more resilient."

# 10. Create the core sync logic script (No changes from v5)
# ------------------------------------------------------------------------------
echo "⚙️ Creating src/sync_logic.py..."
cat << 'EOF' > src/sync_logic.py
import requests
import time
from bs4 import BeautifulSoup
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import logging
from urllib.parse import quote

class BookSyncAutomation:
    def __init__(self, goodreads_user_id, storygraph_email, storygraph_password):
        self.goodreads_user_id = goodreads_user_id
        self.storygraph_email = storygraph_email
        self.storygraph_password = storygraph_password
        self.driver = None

    def get_recently_read_goodreads(self):
        """Fetch recently read books from Goodreads RSS feed"""
        logging.info("Fetching Goodreads RSS feed...")
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'}
        rss_url = f"https://www.goodreads.com/user/updates_rss/{self.goodreads_user_id}"
        response = requests.get(rss_url, headers=headers, timeout=30)
        if response.status_code != 200:
            raise Exception(f"Failed to access RSS feed, status code: {response.status_code}")

        soup = BeautifulSoup(response.text, 'lxml-xml')
        items = soup.find_all('item')
        logging.info(f"Found {len(items)} total items in RSS feed.")
        
        recent_books = []
        for item in items:
            try:
                desc_text = item.find('description').get_text(strip=True)
                if "read" in desc_text or "finished reading" in desc_text:
                    item_title = item.find('title').text.strip()
                    book_title = item_title.split(', ')[0]
                    if " (" in book_title:
                        book_title = book_title.split(" (")[0].strip()

                    pub_date = datetime.strptime(item.find('pubDate').text, '%a, %d %b %Y %H:%M:%S %z')
                    book = {'title': book_title, 'date_read': pub_date}
                    if book not in recent_books:
                        recent_books.append(book)
                        logging.info(f"Found book to sync: {book_title} (Read on: {pub_date.strftime('%Y-%m-%d')})")
            except Exception as e:
                logging.warning(f"Could not process RSS item: {e}")
        return recent_books

    def initialize_browser(self):
        """Initialize a local headless Chrome browser inside the container"""
        if not self.driver:
            logging.info("Initializing local headless Chrome browser...")
            chrome_options = webdriver.ChromeOptions()
            chrome_options.add_argument("--headless")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            
            # The executable_path is no longer needed if chromedriver is in the PATH
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.implicitly_wait(15)
            logging.info("Headless browser initialized.")

    def login_to_storygraph(self):
        """Login to StoryGraph with improved waits and verification"""
        self.initialize_browser()
        try:
            logging.info("Navigating to StoryGraph login page...")
            self.driver.get("https://app.thestorygraph.com/users/sign_in")
            
            if "/users/sign_in" not in self.driver.current_url:
                logging.info("Already logged in.")
                return

            email_field = WebDriverWait(self.driver, 20).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='email']")))
            email_field.send_keys(self.storygraph_email)
            self.driver.find_element(By.CSS_SELECTOR, "input[type='password']").send_keys(self.storygraph_password)
            self.driver.find_element(By.XPATH, "//button[contains(text(), 'Sign in')]").click()

            WebDriverWait(self.driver, 30).until(EC.url_contains("app.thestorygraph.com/"))
            if "/sign_in" in self.driver.current_url:
                 raise Exception("Login failed - still on sign-in page.")
            logging.info("Successfully logged into StoryGraph.")
        except Exception as e:
            self.driver.save_screenshot("logs/login_error.png")
            raise

    def check_book_exists(self, book):
        """Check if book already exists in StoryGraph reading journal"""
        self.driver.get("https://app.thestorygraph.com/journal")
        time.sleep(3)
        if book['title'].lower() in self.driver.page_source.lower():
            logging.info(f"Book '{book['title']}' already exists.")
            return True
        return False
    
    def js_click(self, element):
        self.driver.execute_script("arguments[0].click();", element)

    def update_book_status(self, book):
        if self.check_book_exists(book): return
        logging.info(f"Adding '{book['title']}' to StoryGraph...")
        self.driver.get(f"https://app.thestorygraph.com/browse?search_term={quote(book['title'])}")
        try:
            container = WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.book-pane-content-container")))
            self.js_click(container.find_element(By.CSS_SELECTOR, "button.expand-dropdown-button"))
            time.sleep(1)
            self.js_click(WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "div.read-status-dropdown-content form[action*='status=read'] button"))))
            WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.NAME, "read_instance[year]")))
            
            date = book['date_read']
            self.driver.execute_script(f"document.getElementById('read_instance_day').value = '{date.day}';")
            self.driver.execute_script(f"document.getElementsByName('read_instance[month]')[0].value = '{date.month}';")
            self.driver.execute_script(f"document.getElementsByName('read_instance[year]')[0].value = '{date.year}';")
            time.sleep(1)
            self.js_click(self.driver.find_element(By.CSS_SELECTOR, "input[type='submit'][value='Update']"))
            time.sleep(4)
            logging.info(f"✅ Successfully added '{book['title']}'")
        except Exception as e:
            self.driver.save_screenshot(f"logs/book_error_{book['title'].replace(' ', '_')}.png")
            raise Exception(f"Failed to process '{book['title']}': {e}")

    def sync_books(self):
        processed_titles = []
        try:
            recent_books = self.get_recently_read_goodreads()
            if not recent_books:
                logging.info("No new books to sync.")
                return []
            self.login_to_storygraph()
            for book in recent_books:
                try:
                    logging.info(f"\n--- Processing: '{book['title']}' ---")
                    self.update_book_status(book)
                    processed_titles.append(book['title'])
                    time.sleep(5)
                except Exception as e:
                    logging.error(f"Failed to process '{book['title']}': {e}. Continuing.")
            return processed_titles
        finally:
            if self.driver:
                self.driver.quit()
                self.driver = None
EOF
echo "✅ src/sync_logic.py created."

# 11. Create the Web UI Flask App (No changes from v5)
# ------------------------------------------------------------------------------
echo "🌐 Creating src/webui/app.py..."
cat << 'EOF' > src/webui/app.py
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
EOF
echo "✅ src/webui/app.py created."

# 12. Create the HTML Template for the Web UI (No changes from v5)
# ------------------------------------------------------------------------------
echo "🎨 Creating src/webui/templates/index.html..."
cat << 'EOF' > src/webui/templates/index.html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ebooks Manager Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { font-family: 'Inter', sans-serif; }
        .log-viewer { white-space: pre-wrap; word-wrap: break-word; font-family: 'Courier New', Courier, monospace; }
        .card { backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); }
        .status-success { color: #22c55e; }
        .status-failure { color: #ef4444; }
        .btn-disabled { opacity: 0.5; cursor: not-allowed; }
    </style>
    <link rel="stylesheet" href="https://rsms.me/inter/inter.css">
</head>
<body class="bg-gray-900 text-gray-200">
    <div class="container mx-auto p-4 md:p-8">
        <header class="mb-8 text-center">
            <h1 class="text-4xl font-bold text-white mb-2">📚 Ebooks Manager Dashboard</h1>
            <p class="text-gray-400">Sync status for Goodreads to StoryGraph</p>
        </header>

        <main class="grid grid-cols-1 lg:grid-cols-3 gap-8">
            <!-- Left Column: History & Recommendations -->
            <div class="lg:col-span-2 space-y-8">
                <!-- Sync History -->
                <section id="history" class="card bg-gray-800/50 p-6 rounded-lg shadow-lg">
                    <div class="flex justify-between items-center mb-4 border-b border-gray-700 pb-2">
                         <h2 class="text-2xl font-semibold text-white">Sync History</h2>
                         <button id="sync-now-btn" class="bg-green-600 hover:bg-green-700 text-white font-bold py-2 px-4 rounded-lg transition duration-300">
                            🔄 Sync Now
                        </button>
                    </div>
                    <div class="overflow-x-auto max-h-96">
                        <table class="w-full text-left">
                            <thead class="sticky top-0 bg-gray-800">
                                <tr>
                                    <th class="p-3">Timestamp</th>
                                    <th class="p-3">Status</th>
                                    <th class="p-3">Details</th>
                                </tr>
                            </thead>
                            <tbody id="history-table-body">
                                <tr><td colspan="3" class="p-4 text-center">Loading history...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </section>
                
                <!-- Recommendations -->
                <section id="recommendations" class="card bg-gray-800/50 p-6 rounded-lg shadow-lg">
                    <div class="flex justify-between items-center mb-4 border-b border-gray-700 pb-2">
                        <h2 class="text-2xl font-semibold text-white">AI Book Recommendations</h2>
                        <button id="get-recs-btn" class="bg-indigo-600 hover:bg-indigo-700 text-white font-bold py-2 px-4 rounded-lg transition duration-300">
                            ✨ Get Recs
                        </button>
                    </div>
                    <div id="recs-container" class="space-y-4">
                        <p class="text-gray-400">Click the button to get book recommendations based on your latest reads!</p>
                    </div>
                </section>
            </div>

            <!-- Right Column: Logs -->
            <div class="lg:col-span-1">
                <section id="logs" class="card bg-gray-800/50 p-6 rounded-lg shadow-lg h-full">
                    <h2 class="text-2xl font-semibold mb-4 text-white border-b border-gray-700 pb-2">Live Log Viewer</h2>
                    <div id="log-container" class="bg-gray-900 p-4 rounded-md h-[40rem] overflow-y-auto log-viewer text-sm">
                        Loading logs...
                    </div>
                </section>
            </div>
        </main>
    </div>

    <script>
        const historyTableBody = document.getElementById('history-table-body');
        const logContainer = document.getElementById('log-container');
        const recsContainer = document.getElementById('recs-container');
        const getRecsBtn = document.getElementById('get-recs-btn');
        const syncNowBtn = document.getElementById('sync-now-btn');

        function formatTimestamp(isoString) {
            return isoString ? new Date(isoString).toLocaleString() : 'N/A';
        }

        async function fetchHistory() {
            try {
                const response = await fetch('/api/history');
                const history = await response.json();
                historyTableBody.innerHTML = '';
                if (history.length === 0) {
                    historyTableBody.innerHTML = '<tr><td colspan="3" class="p-4 text-center">No history found.</td></tr>';
                    return;
                }
                history.forEach(entry => {
                    const statusClass = entry.status === 'Success' ? 'status-success' : 'status-failure';
                    const books = entry.books_processed || [];
                    const details = entry.status === 'Success' 
                        ? `${books.length} book(s) synced: ${books.join(', ') || 'None'}`
                        : `Error: ${entry.error || 'Unknown'}`;
                    historyTableBody.innerHTML += `<tr class="border-b border-gray-700 hover:bg-gray-700/50"><td class="p-3">${formatTimestamp(entry.timestamp)}</td><td class="p-3 font-semibold ${statusClass}">${entry.status}</td><td class="p-3 text-gray-400">${details}</td></tr>`;
                });
            } catch (error) {
                historyTableBody.innerHTML = '<tr><td colspan="3" class="p-4 text-center text-red-500">Failed to load history.</td></tr>';
            }
        }

        async function fetchLogs() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();
                logContainer.textContent = data.log_content;
                logContainer.scrollTop = logContainer.scrollHeight;
            } catch (error) {
                logContainer.textContent = 'Failed to load logs.';
            }
        }
        
        async function triggerSync() {
            syncNowBtn.disabled = true;
            syncNowBtn.classList.add('btn-disabled');
            syncNowBtn.textContent = 'Syncing...';
            try {
                await fetch('/api/sync-now', { method: 'POST' });
                setTimeout(() => { fetchLogs(); fetchHistory(); }, 5000);
            } finally {
                 setTimeout(() => {
                    syncNowBtn.disabled = false;
                    syncNowBtn.classList.remove('btn-disabled');
                    syncNowBtn.textContent = '🔄 Sync Now';
                }, 5000);
            }
        }

        async function fetchRecommendations() {
            recsContainer.innerHTML = '<p class="text-indigo-400">Getting recommendations from the AI wizard...</p>';
            getRecsBtn.disabled = true;
            getRecsBtn.classList.add('btn-disabled');
            try {
                const response = await fetch('/api/recommendations');
                const data = await response.json();
                if (data.error) throw new Error(data.error);
                if (typeof data.recommendations === 'string') {
                     recsContainer.innerHTML = `<p class="text-gray-300">${data.recommendations}</p>`;
                     return;
                }
                recsContainer.innerHTML = '';
                data.recommendations.forEach(rec => {
                    recsContainer.innerHTML += `<div class="bg-gray-700/50 p-4 rounded-lg"><h3 class="font-bold text-lg text-white">${rec.title}</h3><p class="text-sm text-gray-400 mb-2">by ${rec.author}</p><p class="text-gray-300"><em>"${rec.reason}"</em></p></div>`;
                });
            } catch (error) {
                recsContainer.innerHTML = `<p class="text-red-500">Failed to get recommendations: ${error.message}</p>`;
            } finally {
                getRecsBtn.disabled = false;
                getRecsBtn.classList.remove('btn-disabled');
            }
        }

        // Initial & periodic data fetch
        fetchHistory();
        fetchLogs();
        setInterval(() => { fetchHistory(); fetchLogs(); }, 30000);
        
        getRecsBtn.addEventListener('click', fetchRecommendations);
        syncNowBtn.addEventListener('click', triggerSync);
    </script>
</body>
</html>
EOF
echo "✅ src/webui/templates/index.html created."

# 13. Final Steps
# ------------------------------------------------------------------------------
echo " finalizing setup..."

cp config/config.json.template config/config.json

echo -e "\n\n"
echo "🎉====================================================🎉"
echo "      Ebooks Manager Setup Complete! (v6)"
echo "🎉====================================================🎉"
echo ""
echo "This version fixes the scheduler crash on startup."
echo ""
echo "What's Next:"
echo "1. Your container is already running. First, stop it:"
echo "   docker-compose down"
echo ""
echo "2. Re-run this setup script to apply the fix:"
echo "   ./setup.sh"
echo ""
echo "3. IMPORTANT: Edit the 'config/config.json' file with your details."
echo ""
echo "4. Rebuild and restart the container:"
echo "   docker-compose up --build -d"
echo ""
echo "Now, the app will start correctly even before you've added your"
echo "credentials, and the sync will begin once you have."
echo ""
echo "Happy reading! 📚"

