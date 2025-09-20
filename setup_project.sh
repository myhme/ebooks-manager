#!/bin/bash

# ==============================================================================
# setup_project.sh
#
# Creates a complete project structure for "ebooks-manager" with production-ready
# improvements to login handling, web UI non-blocking sync, better error handling,
# cookie-first login, and other reliability improvements.
#
# Usage:
#   chmod +x setup_project.sh
#   ./setup_project.sh
#
# ==============================================================================

set -euo pipefail

PROJECT_NAME="ebooks-manager"
ROOT_DIR="$(pwd)/${PROJECT_NAME}"

echo "Creating the project structure for '$PROJECT_NAME' in: $ROOT_DIR"
mkdir -p "$ROOT_DIR"
cd "$ROOT_DIR"

# ---------------------------
# Basic directories
# ---------------------------
mkdir -p "/mnt/data/build/ebooks-manager"
mkdir -p "data/cache/logins/goodreads"
mkdir -p "data/cache/logins/cwa"
mkdir -p "data/cache/webpages/goodreads/ebooks"
mkdir -p "data/databases"
mkdir -p "logs/screenshots"
mkdir -p "scripts"
mkdir -p "config"
mkdir -p "src/scrapers"
mkdir -p "src/api"
mkdir -p "src/webui/templates"
mkdir -p "src/utils"

# ---------------------------
# .env.example
# ---------------------------
cat <<'EOF' > ".env.example"
# User and Group IDs
PUID=1000
PGID=1000

# Timezone
TZ=Asia/Kolkata

# Logging
LOG_LEVEL=DEBUG

# FlareSolverr
FLARESOLVERR_URL=http://flaresolverr:8191/v1

# Flask
FLASK_HOST=0.0.0.0
FLASK_PORT=5002

# Application
DRY_RUN=yes
PYTHONUNBUFFERED=1
WEBPAGE_CACHE_DAYS=30
EOF

# ---------------------------
# scripts/entrypoint.sh
# ---------------------------
cat <<'EOF' > "scripts/entrypoint.sh"
#!/bin/bash
set -euo pipefail

# Entrypoint: run migrations/bootstrap, start gunicorn and run scheduler in background
echo "Running bootstrap..."
if ! python /app/src/bootstrap.py; then
    echo "Bootstrap failed, exiting."
    exit 1
fi

# Ensure config exists
if [ ! -f /app/config/config.json ]; then
    if [ -f /app/config/config.json.template ]; then
        cp /app/config/config.json.template /app/config/config.json
        echo "Created config.json from template. Please update it with your credentials or use environment variables."
        exit 1
    else
        echo "Missing config.json.template; please provide config/config.json."
        exit 1
    fi
fi

# Start Gunicorn (foreground) and scheduler as background process via job runner
echo "Starting job runner (scheduler) in background..."
python3 /app/src/job_runner.py &

echo "Starting Gunicorn..."
exec gunicorn --bind 0.0.0.0:5002 --workers 2 --threads 4 --timeout 120 --log-file /app/logs/gunicorn.log --log-level info src.webui.app:app
EOF
chmod +x scripts/entrypoint.sh

# ---------------------------
# scripts/script.sh
# ---------------------------
cat <<'EOF' > "scripts/script.sh"
#!/bin/bash
set -euo pipefail
exec python3 /app/src/main.py
EOF
chmod +x scripts/script.sh

# ---------------------------
# config/config.json.template
# ---------------------------
cat <<'EOF' > "config/config.json.template"
{
  "goodreads_username": "your_goodreads_email",
  "goodreads_password": "your_goodreads_password",
  "goodreads_user_id": "your_goodreads_user_id",
  "cwa_api_url": "http://calibre-web:8084/request/api",
  "cwa_username": "your_cwa_username",
  "cwa_password": "your_cwa_password",
  "database_path": "/app/data/databases/goodreads.db",
  "log_file": "/app/logs/sync_log.txt",
  "history_file": "/app/logs/history.json",
  "cache_dir": "/app/data/cache"
}
EOF

# ---------------------------
# requirements.txt
# ---------------------------
cat <<'EOF' > "requirements.txt"
requests==2.31.0
beautifulsoup4==4.12.2
flask==3.0.3
selenium==4.23.1
gunicorn==20.1.0
apscheduler==3.10.4
EOF

# ---------------------------
# Dockerfile (keeps previous but slightly hardened)
# ---------------------------
cat <<'EOF' > "Dockerfile"
FROM python:3.9-slim

WORKDIR /app

# Create non-root user
RUN useradd -m -r -u 1000 appuser && \
    mkdir -p /home/appuser/.cache && \
    chown -R appuser:appuser /app /home/appuser

# Install essential packages including chromium and driver
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg unzip ca-certificates jq chromium chromium-driver \
    libglib2.0-0 libnss3 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Ensure chromedriver permissions
RUN if [ ! -f /usr/bin/chromedriver ]; then echo "ChromeDriver missing"; exit 1; fi && \
    chmod 755 /usr/bin/chromedriver && chown appuser:appuser /usr/bin/chromedriver

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip cache purge

# Copy app
COPY . .

# Ensure logs/data directories are writable
RUN mkdir -p /app/logs /app/data && chown -R appuser:appuser /app

ENV PYTHONPATH=/app \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    PATH="/home/appuser/.local/bin:$PATH" \
    TZ=Asia/Kolkata

USER appuser

CMD ["/app/scripts/entrypoint.sh"]
EOF

# ---------------------------
# src/bootstrap.py
# ---------------------------
cat <<'EOF' > "src/bootstrap.py"
import os
import json
import sys
from pathlib import Path

REQUIRED_CONFIG_KEYS = [
    'goodreads_username', 'goodreads_password', 'goodreads_user_id',
    'cwa_api_url', 'cwa_username', 'cwa_password',
    'database_path', 'log_file', 'history_file', 'cache_dir'
]

def check_and_create_app_files():
    print("Running bootstrap checks...")
    try:
        app_dir = Path(os.getenv('APP_DIR', '/app'))
        config_dir = app_dir / 'config'
        log_dir = app_dir / 'logs'
        data_dir = app_dir / 'data'

        dirs = [
            config_dir,
            log_dir,
            log_dir / 'screenshots',
            data_dir,
            data_dir / 'databases',
            data_dir / 'cache' / 'logins' / 'goodreads',
            data_dir / 'cache' / 'webpages' / 'goodreads' / 'ebooks'
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
            print(f"Ensured directory: {d}")

        config_file = config_dir / 'config.json'
        template = config_dir / 'config.json.template'
        if not config_file.exists():
            if template.exists():
                config_file.write_text(template.read_text())
                print(f"Copied config from template to {config_file}")
            else:
                default = {
                    "goodreads_username": "",
                    "goodreads_password": "",
                    "goodreads_user_id": "",
                    "cwa_api_url": "http://calibre-web:8084/request/api",
                    "cwa_username": "",
                    "cwa_password": "",
                    "database_path": "/app/data/databases/goodreads.db",
                    "log_file": "/app/logs/sync_log.txt",
                    "history_file": "/app/logs/history.json",
                    "cache_dir": "/app/data/cache"
                }
                config_file.write_text(json.dumps(default, indent=2))
                print(f"Wrote default config to {config_file}")

        # Validate config
        try:
            c = json.loads(config_file.read_text())
            missing = [k for k in REQUIRED_CONFIG_KEYS if k not in c or c[k] is None]
            if missing:
                raise ValueError(f"Missing keys: {missing}")
            print("Config validated.")
        except Exception as e:
            print(f"Invalid config: {e}")
            sys.exit(1)

        # Ensure files exist
        for f in [log_dir / 'sync_log.txt', log_dir / 'history.json', data_dir / 'databases' / 'goodreads.db']:
            if not f.exists():
                f.parent.mkdir(parents=True, exist_ok=True)
                f.touch()
                print(f"Created {f}")

        print("Bootstrap checks complete.")
    except Exception as exc:
        print(f"Bootstrap failed: {exc}")
        sys.exit(1)

if __name__ == "__main__":
    check_and_create_app_files()
EOF

# ---------------------------
# src/api/cwa_client.py
# ---------------------------
cat <<'EOF' > "src/api/cwa_client.py"
import requests
import json
from pathlib import Path
import logging
import time

class CWAClient:
    def __init__(self, api_url, username, password, cache_dir):
        self.api_url = api_url
        self.auth = (username, password)
        self.cache_dir = Path(cache_dir) / 'cwa_book_downloader' / 'search_results'
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def search_book(self, query, language='en'):
        try:
            search_url = f"{self.api_url}/search"
            response = requests.post(search_url, json={'query': query, 'language': language}, auth=self.auth, timeout=30)
            response.raise_for_status()
            results = response.json()
            cache_file = self.cache_dir / f"{query.replace(' ', '_')}.json"
            cache_file.write_text(json.dumps(results))
            return results
        except requests.RequestException as e:
            logging.error(f"CWA search failed: {e}", exc_info=True)
            return None

    def request_download(self, result_id, book_format='epub'):
        try:
            download_url = f"{self.api_url}/download"
            response = requests.post(download_url, json={'result_id': result_id, 'format': book_format}, auth=self.auth, timeout=30)
            response.raise_for_status()
            return response.json().get('download_id')
        except requests.RequestException as e:
            logging.error(f"Download request failed: {e}", exc_info=True)
            return None

    def check_download_status(self, download_id, max_attempts=10, wait_seconds=10):
        status_url = f"{self.api_url}/status/{download_id}"
        for _ in range(max_attempts):
            try:
                r = requests.get(status_url, auth=self.auth, timeout=20)
                r.raise_for_status()
                status = r.json().get('status')
                if status == 'success':
                    return True
                if status == 'failure':
                    return False
                time.sleep(wait_seconds)
            except requests.RequestException as e:
                logging.error(f"Status check failed: {e}", exc_info=True)
                time.sleep(wait_seconds)
        return False
EOF

# ---------------------------
# src/utils/logger.py
# ---------------------------
cat <<'EOF' > "src/utils/logger.py"
import logging
import sys
from pathlib import Path
import os

def setup_logger(log_file, level='INFO'):
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch(exist_ok=True)
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Clear handlers
    for h in logging.root.handlers[:]:
        logging.root.removeHandler(h)

    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))
    file_handler.setLevel(log_level)

    st = logging.StreamHandler(sys.stdout)
    st.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    st.setLevel(log_level)

    logging.basicConfig(level=log_level, handlers=[file_handler, st], force=True)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("selenium").setLevel(logging.WARNING)
    return logging.getLogger()
EOF

# ---------------------------
# src/utils/config_loader.py
# ---------------------------
cat <<'EOF' > "src/utils/config_loader.py"
import json
import os

def load_config(config_path='/app/config/config.json'):
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        # Override with env vars
        config['goodreads_username'] = os.getenv('GOODREADS_USERNAME', config.get('goodreads_username'))
        config['goodreads_password'] = os.getenv('GOODREADS_PASSWORD', config.get('goodreads_password'))
        config['goodreads_user_id'] = os.getenv('GOODREADS_USER_ID', config.get('goodreads_user_id'))
        config['cwa_api_url'] = os.getenv('CWA_API_URL', config.get('cwa_api_url'))
        config['cwa_username'] = os.getenv('CWA_USERNAME', config.get('cwa_username'))
        config['cwa_password'] = os.getenv('CWA_PASSWORD', config.get('cwa_password'))
        config['cache_dir'] = os.getenv('CACHE_DIR', config.get('cache_dir', '/app/data/cache'))
        return config
    except FileNotFoundError:
        raise Exception(f"Config file {config_path} not found")
    except json.JSONDecodeError:
        raise Exception(f"Invalid JSON in {config_path}")
EOF

# ---------------------------
# src/utils/database.py
# ---------------------------
cat <<'EOF' > "src/utils/database.py"
import sqlite3
import json
from pathlib import Path

class Database:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS books (
                    goodreads_id TEXT PRIMARY KEY,
                    title TEXT,
                    author TEXT,
                    json_details TEXT
                )
            ''')
            conn.commit()

    def save_book(self, details):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            json_details = json.dumps(details)
            cursor.execute('''
                INSERT OR REPLACE INTO books (goodreads_id, title, author, json_details)
                VALUES (?, ?, ?, ?)
            ''', (details['goodreads_id'], details.get('title'), details.get('author'), json_details))
            conn.commit()
EOF

# ---------------------------
# src/scrapers/goodreads.py (IMPROVED)
# ---------------------------
cat <<'EOF' > "src/scrapers/goodreads.py"
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from bs4 import BeautifulSoup
from pathlib import Path
import json
import logging
import time
import os
import requests
import pickle
from datetime import datetime
from urllib.parse import urljoin

from src.utils.database import Database
from src.api.cwa_client import CWAClient

LOG = logging.getLogger(__name__)

class GoodreadsScraper:
    def __init__(self, config):
        self.config = config
        self.user_id = config.get('goodreads_user_id')
        self.goodreads_email = config.get('goodreads_username')
        self.goodreads_password = config.get('goodreads_password')
        self.shelf_name = 'to-download'
        self.base_url = 'https://www.goodreads.com'
        self.cache_dir = Path(config.get('cache_dir', '/app/data/cache')) / 'webpages' / 'goodreads' / 'ebooks'
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_dir = Path(config.get('log_file', '/app/logs/sync_log.txt')).parent / 'screenshots'
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.cookie_file = Path(config.get('cache_dir', '/app/data/cache')) / 'logins' / 'goodreads' / 'cookies.pkl'
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        self.db = Database(config.get('database_path'))
        self.cwa_client = CWAClient(
            config.get('cwa_api_url'),
            config.get('cwa_username'),
            config.get('cwa_password'),
            config.get('cache_dir')
        )
        self.is_debug = os.getenv('LOG_LEVEL', 'INFO').upper() == 'DEBUG'
        self.dry_run = os.getenv('DRY_RUN', 'no').lower() == 'yes'
        self.driver = None
        self.logged_in = False
        self._selenium_available = True

    def _init_driver(self):
        if self.driver:
            return self.driver
        try:
            options = Options()
            # headless recommended for server, but allow override via env var
            headless = os.getenv('HEADLESS', '1') == '1'
            if headless:
                options.add_argument('--headless=new')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--disable-extensions')
            options.add_argument('--window-size=1920,1080')
            ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36"
            options.add_argument(f'user-agent={ua}')
            chromium_bin = os.getenv('CHROMIUM_BIN', '/usr/bin/chromium')
            if Path(chromium_bin).exists():
                options.binary_location = chromium_bin

            chromedriver_path = os.getenv('CHROMEDRIVER_PATH', '/usr/bin/chromedriver')
            if not Path(chromedriver_path).exists():
                LOG.error("ChromeDriver not found at %s", chromedriver_path)
                self._selenium_available = False
                return None

            service = Service(executable_path=chromedriver_path)
            self.driver = webdriver.Chrome(service=service, options=options)
            LOG.info("Selenium driver initialized")
            return self.driver
        except WebDriverException as e:
            LOG.error("Failed to initialize driver: %s", e, exc_info=True)
            self._selenium_available = False
            return None

    def _close_driver(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
        self.driver = None
        self.logged_in = False

    def _take_screenshot(self, name, force=False):
        if not self.driver or not (self.is_debug or force):
            return
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = self.screenshot_dir / f"{name}_{ts}.png"
            # attempt full page by adjusting window size
            try:
                height = self.driver.execute_script("return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);")
                width = self.driver.execute_script("return Math.max(document.body.scrollWidth, document.documentElement.scrollWidth);")
                self.driver.set_window_size(min(2000, int(width)), min(3000, int(height)))
                time.sleep(0.3)
            except Exception:
                pass
            self.driver.save_screenshot(str(path))
            LOG.info("Saved screenshot: %s", path)
        except Exception as e:
            LOG.warning("Screenshot failed: %s", e)

    def _save_cookies(self):
        if not self.driver:
            return
        try:
            with open(self.cookie_file, 'wb') as f:
                pickle.dump(self.driver.get_cookies(), f)
            LOG.info("Saved selenium cookies to %s", self.cookie_file)
        except Exception as e:
            LOG.error("Failed to save cookies: %s", e, exc_info=True)

    def _load_cookies_to_selenium(self):
        if not self.driver or not self.cookie_file.exists():
            LOG.debug("No cookies available for selenium load")
            return False
        try:
            with open(self.cookie_file, 'rb') as f:
                cookies = pickle.load(f)
            for c in cookies:
                # Some cookies have invalid expiry or domain; handle carefully
                try:
                    c.pop('sameSite', None)
                    if 'expiry' in c and isinstance(c['expiry'], float):
                        c['expiry'] = int(c['expiry'])
                    # Fix leading dot in domain if necessary
                    if 'domain' in c and c['domain'].startswith('.'):
                        c['domain'] = c['domain'].lstrip('.')
                    self.driver.add_cookie(c)
                except Exception:
                    LOG.debug("Skipping cookie due to invalid format: %s", c)
            LOG.info("Loaded cookies into selenium")
            return True
        except Exception as e:
            LOG.error("Failed to load cookies for selenium: %s", e, exc_info=True)
            return False

    def _load_cookies_into_requests(self):
        # returns a requests.Session with cookies loaded if available
        s = requests.Session()
        if not self.cookie_file.exists():
            return s
        try:
            with open(self.cookie_file, 'rb') as f:
                cookies = pickle.load(f)
            for c in cookies:
                cookie_dict = {
                    'domain': c.get('domain', '.goodreads.com'),
                    'name': c.get('name'),
                    'value': c.get('value'),
                    'path': c.get('path', '/'),
                }
                s.cookies.set(cookie_dict['name'], cookie_dict['value'], domain=cookie_dict['domain'], path=cookie_dict['path'])
            LOG.info("Loaded cookies into requests session")
        except Exception as e:
            LOG.warning("Failed to load cookies into requests: %s", e)
        return s

    def _verify_logged_in_selenium(self):
        try:
            WebDriverWait(self.driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href="/user/sign_out"], a[href*="/user/show/"]'))
            )
            return True
        except TimeoutException:
            return False

    def login(self):
        # Cookie-first approach (selenium)
        if self.logged_in:
            LOG.info("Already logged in")
            return True

        driver = self._init_driver()
        if not driver:
            # Selenium not available; rely on cookies + requests fallback
            LOG.warning("Selenium not available; will attempt requests fallback using cookies.")
            return False

        # Try to use cookies first
        try:
            driver.get(self.base_url)
            if self.cookie_file.exists():
                LOG.info("Attempting cookie-based login for selenium")
                self._load_cookies_to_selenium()
                driver.refresh()
                time.sleep(1)
                if self._verify_logged_in_selenium():
                    LOG.info("Logged in using saved cookies (selenium)")
                    self.logged_in = True
                    return True
                else:
                    LOG.info("Saved cookies invalid in selenium, proceeding to full login")
        except Exception as e:
            LOG.warning("Cookie-based login attempt failed: %s", e, exc_info=True)

        # Full login flow (handle multiple variants)
        tries = 3
        for attempt in range(1, tries + 1):
            try:
                LOG.info("Navigating to sign-in page (attempt %d/%d)", attempt, tries)
                # Goodreads may provide multiple ways to sign in. Navigate to the canonical sign-in URL.
                driver.get(f"{self.base_url}/user/sign_in")
                self._take_screenshot("signin_page")

                # Try the common flows in order of preference
                success = False

                # Option A: Prize: "Sign in with email" button (Goodreads) -> Amazon form appears
                try:
                    LOG.debug("Trying 'Sign in with email' button selector")
                    btn = WebDriverWait(driver, 6).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[contains(normalize-space(.),'Sign in with email') or contains(normalize-space(.),'Sign in with Amazon')]"))
                    )
                    btn.click()
                    LOG.debug("Clicked sign in with email/amazon")
                    time.sleep(0.8)
                except TimeoutException:
                    LOG.debug("'Sign in with email' button not found; continuing to locate direct fields")

                # Option B: Look for Amazon-style login fields directly
                try:
                    email_el = WebDriverWait(driver, 8).until(
                        EC.presence_of_element_located((By.ID, "ap_email"))
                    )
                    password_el = driver.find_element(By.ID, "ap_password")
                    LOG.debug("Found Amazon ap_email/ap_password fields")
                except TimeoutException:
                    # Option C: Goodreads login fields or alternative selectors
                    LOG.debug("ap_email not found; try Goodreads native login forms")
                    try:
                        email_el = WebDriverWait(driver, 6).until(
                            EC.presence_of_element_located((By.NAME, "user[email]"))
                        )
                        password_el = driver.find_element(By.NAME, "user[password]")
                        LOG.debug("Found Goodreads native fields")
                    except TimeoutException:
                        email_el = None
                        password_el = None

                if email_el is None or password_el is None:
                    raise Exception("Login form fields could not be found (ap_email or user[email])")

                # Optional: try to locate "Keep me signed in" checkbox and tick it
                try:
                    # Common amazon checkbox ids/names/labels
                    for sel in [
                        (By.ID, "rememberMe"),
                        (By.NAME, "rememberMe"),
                        (By.XPATH, "//input[@type='checkbox' and (contains(@id,'remember') or contains(@name,'remember'))]"),
                        (By.XPATH, "//label[contains(., 'Keep me signed in')]/preceding-sibling::input[@type='checkbox']"),
                        (By.XPATH, "//span[contains(., 'Keep me signed in')]/preceding::input[1][@type='checkbox']")
                    ]:
                        try:
                            el = driver.find_element(*sel)
                            if el and not el.is_selected():
                                el.click()
                                LOG.info("Checked 'Keep me signed in' checkbox using selector %s", sel)
                                break
                        except Exception:
                            continue
                except Exception:
                    LOG.debug("Could not check 'Keep me signed in' checkbox")

                # Fill and submit
                LOG.info("Entering credentials (masked)")
                email_el.clear()
                email_el.send_keys(self.goodreads_email)
                password_el.clear()
                password_el.send_keys(self.goodreads_password)

                # Submit - try typical submit buttons
                submitted = False
                for submit_selector in [
                    (By.ID, "signInSubmit"),
                    (By.XPATH, "//input[@type='submit' and (@id='signInSubmit' or contains(@value,'Sign-In') or contains(@value,'Sign in'))]"),
                    (By.XPATH, "//button[contains(., 'Sign in') or contains(., 'Sign-In') or contains(., 'Sign in with Amazon')]")
                ]:
                    try:
                        btn = driver.find_element(*submit_selector)
                        btn.click()
                        submitted = True
                        LOG.debug("Clicked submit: %s", submit_selector)
                        break
                    except Exception:
                        continue

                if not submitted:
                    # Last resort: press ENTER in password field
                    from selenium.webdriver.common.keys import Keys
                    password_el.send_keys(Keys.RETURN)
                    LOG.debug("Submitted via Enter key")

                # Wait for redirect away from signin; Goodreads sometimes does multi-step
                def login_success_condition(d):
                    cur = d.current_url.lower()
                    # success: user is on goodreads and not on amazon signin pages
                    if "goodreads.com" in cur and "/ap/signin" not in cur and "signin" not in cur:
                        return True
                    # presence of sign_out link indicates logged in
                    try:
                        if d.find_element(By.CSS_SELECTOR, 'a[href="/user/sign_out"]'):
                            return True
                    except Exception:
                        pass
                    return False

                WebDriverWait(driver, 30).until(login_success_condition)
                # success
                self._take_screenshot("login_success")
                self._save_cookies()
                self.logged_in = True
                LOG.info("Successfully logged into Goodreads (selenium)")
                return True

            except Exception as exc:
                LOG.warning("Login attempt %d failed: %s", attempt, exc, exc_info=True)
                self._take_screenshot(f"login_error_attempt_{attempt}", force=True)
                time.sleep(3 + attempt * 2)
                if attempt == tries:
                    LOG.error("Exhausted login attempts")
                    break
                continue

        # Final fallback: close selenium and let caller try requests fallback using cookies
        self._close_driver()
        return False

    def _fetch_shelf_with_requests(self, shelf_name):
        # Attempts to fetch shelf HTML using requests.Session and cookies (fallback)
        session = self._load_cookies_into_requests()
        shelf_url = f"{self.base_url}/review/list/{self.user_id}?shelf={shelf_name}"
        try:
            r = session.get(shelf_url, timeout=20)
            if r.status_code == 200 and "Sign in" not in r.text:
                LOG.info("Fetched shelf via requests fallback")
                return r.text
            LOG.warning("Requests fallback couldn't fetch shelf (status=%s)", r.status_code)
            return None
        except Exception as e:
            LOG.error("Requests fetch failed: %s", e, exc_info=True)
            return None

    def get_goodreads_books_from_shelf(self, shelf_name):
        LOG.info("Fetching books from shelf: %s", shelf_name)

        # 1) Try Selenium cookie-based login or full login
        if self._selenium_available:
            ok = False
            try:
                ok = self.login()
            except Exception as e:
                LOG.warning("Selenium login error: %s", e, exc_info=True)
                ok = False

            if ok and self.driver:
                try:
                    shelf_url = f"{self.base_url}/review/list/{self.user_id}?shelf={shelf_name}"
                    LOG.debug("Navigating selenium to: %s", shelf_url)
                    self.driver.get(shelf_url)
                    WebDriverWait(self.driver, 20).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "table.bookalike, div.gr_book"))
                    )
                    self._take_screenshot(f"fetch_shelf_{shelf_name}")
                    page_source = self.driver.page_source
                except Exception as e:
                    LOG.error("Selenium fetch of shelf failed: %s", e, exc_info=True)
                    self._take_screenshot("fetch_shelf_error", force=True)
                    page_source = None
            else:
                page_source = None
        else:
            page_source = None

        # 2) If selenium failed, attempt requests fallback using cookies
        if not page_source:
            LOG.info("Attempting requests fallback to fetch shelf")
            page_source = self._fetch_shelf_with_requests(shelf_name)

        if not page_source:
            LOG.error("Unable to fetch shelf page via selenium or requests fallback")
            return []

        # Save raw HTML for debugging cache
        try:
            fname = self.cache_dir / f"shelf_{shelf_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            fname.write_text(page_source, encoding='utf-8')
            LOG.debug("Saved shelf HTML to %s", fname)
        except Exception:
            pass

        # Parse page
        soup = BeautifulSoup(page_source, 'html.parser')
        books = []
        # table.bookalike structure is typical; also accept divs if modern view
        rows = soup.select('table.bookalike tr') or soup.select('div.leftContainer div.elementList')
        for row in rows:
            try:
                title_elem = row.select_one('td.title .value a') or row.select_one('a.bookTitle') or row.select_one('a.title')
                author_elem = row.select_one('td.author .value a') or row.select_one('a.authorName') or row.select_one('.authorName')
                if not (title_elem and author_elem):
                    continue
                title = title_elem.get_text(strip=True)
                author = author_elem.get_text(strip=True)
                book_url = title_elem.get('href')
                goodreads_id = None
                if book_url:
                    # typical /book/show/ID or /work/ID
                    parts = [p for p in book_url.split('/') if p]
                    # find numeric part
                    for p in reversed(parts):
                        if p.isdigit():
                            goodreads_id = p
                            break
                if not goodreads_id:
                    LOG.debug("Skipping book with no ID: %s", title)
                    continue
                books.append({
                    'goodreads_id': goodreads_id,
                    'title': title,
                    'author': author,
                    'book_url': urljoin(self.base_url, book_url)
                })
            except Exception:
                LOG.debug("Skipping malformed row", exc_info=True)
                continue

        LOG.info("Found %d books on shelf %s", len(books), shelf_name)
        # Cleanup selenium driver to free resources
        try:
            self._close_driver()
        except Exception:
            pass
        return books

    def update_history(self, entry):
        history_path = Path(self.config.get('history_file', '/app/logs/history.json'))
        entry['timestamp'] = datetime.now().isoformat()
        try:
            history = []
            if history_path.exists() and history_path.stat().st_size > 0:
                history = json.loads(history_path.read_text())
            history.append(entry)
            history_path.write_text(json.dumps(history, indent=2))
        except Exception:
            LOG.warning("Failed to update history file", exc_info=True)

    def sync(self):
        books = self.get_goodreads_books_from_shelf(self.shelf_name)
        if not books:
            LOG.warning("No books found, skipping sync")
            return
        for b in books:
            d = {'goodreads_id': b['goodreads_id'], 'title': b['title'], 'author': b['author']}
            self.db.save_book(d)
            self.update_history({'action': 'fetch_shelf', 'book_id': d['goodreads_id'], 'title': d['title'], 'status': 'success'})
            query = f"{d['title']} {d['author']}"
            results = self.cwa_client.search_book(query)
            if not results or not results.get('results'):
                self.update_history({'action':'search','book_id':d['goodreads_id'],'title':d['title'],'status':'no_results'})
                continue
            if self.dry_run:
                self.update_history({'action':'download','book_id':d['goodreads_id'],'title':d['title'],'status':'skipped_dry_run'})
                continue
            best = results['results'][0]
            download_id = self.cwa_client.request_download(best['id'])
            if not download_id:
                continue
            success = self.cwa_client.check_download_status(download_id)
            self.update_history({'action':'download','book_id':d['goodreads_id'],'title':d['title'],'result_id':best['id'],'status':'success' if success else 'failure'})
EOF

# ---------------------------
# src/sync_logic.py
# ---------------------------
cat <<'EOF' > "src/sync_logic.py"
import logging
from src.scrapers.goodreads import GoodreadsScraper
from src.utils.config_loader import load_config

def orchestrate_sync():
    config = load_config()
    logger = logging.getLogger(__name__)
    try:
        scraper = GoodreadsScraper(config)
        logger.info("Starting Goodreads sync")
        scraper.sync()
        logger.info("Goodreads sync finished")
    except Exception as e:
        logger.exception("Sync failed: %s", e)
EOF

# ---------------------------
# src/main.py
# ---------------------------
cat <<'EOF' > "src/main.py"
from src.utils.config_loader import load_config
from src.utils.logger import setup_logger
from src.sync_logic import orchestrate_sync
import os

def main():
    config = load_config()
    setup_logger(config['log_file'], os.getenv('LOG_LEVEL', 'INFO'))
    orchestrate_sync()

if __name__ == "__main__":
    main()
EOF

# ---------------------------
# src/webui/app.py (IMPROVED: non-blocking /sync endpoint + status)
# ---------------------------
cat <<'EOF' > "src/webui/app.py"
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
EOF

# ---------------------------
# src/webui/templates/layout.html (minor improvements + JS)
# ---------------------------
cat <<'EOF' > "src/webui/templates/layout.html"
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>{% block title %}Ebooks Manager{% endblock %}</title>
  <style>
    body { font-family: Arial, Helvetica, sans-serif; background:#f4f7f6; color:#333; padding:1.2rem; }
    .container { max-width:1100px; margin:0 auto; background:#fff; padding:1.6rem; border-radius:8px; box-shadow:0 6px 18px rgba(0,0,0,0.06); }
    nav a { margin-right:1rem; color:#2c3e50; text-decoration:none; }
    button { padding:8px 12px; border-radius:6px; border:1px solid #ddd; background:#f8f8f8; cursor:pointer; }
    button[disabled]{opacity:0.6; cursor:not-allowed;}
    table{width:100%;border-collapse:collapse;margin-top:1rem}
    th,td{border:1px solid #eee;padding:10px;text-align:left}
    .footer{margin-top:1.5rem;color:#888;text-align:center;font-size:0.9rem}
    .sync-running{color:#d35400;font-weight:bold}
  </style>
  {% block head %}{% endblock %}
  <script>
    async function checkSyncState() {
      try {
        const res = await fetch('/sync/status');
        if (!res.ok) return;
        const js = await res.json();
        const btn = document.getElementById('run-sync-btn');
        const status = document.getElementById('sync-state');
        if (btn) btn.disabled = js.running;
        if (status) status.innerText = js.running ? 'Running' : 'Idle';
      } catch (e) { console.log('sync check failed', e); }
    }
    setInterval(checkSyncState, 5000);
    window.addEventListener('load', checkSyncState);
  </script>
</head>
<body>
  <div class="container">
    <h1>{% block header %}Ebooks Manager{% endblock %}</h1>
    <nav>
      <a href="{{ url_for('status') }}">Status</a>
      <a href="{{ url_for('sync') }}">Sync</a>
      <a href="{{ url_for('shelf_view', shelf_name='to-download') }}">To-Download Shelf</a>
    </nav>
    {% block content %}{% endblock %}
    <div class="footer">Ebooks Manager</div>
  </div>
</body>
</html>
EOF

# ---------------------------
# src/webui/templates/sync.html (uses background sync)
# ---------------------------
cat <<'EOF' > "src/webui/templates/sync.html"
{% extends "layout.html" %}
{% block title %}Manual Sync{% endblock %}
{% block header %}Manual Sync{% endblock %}
{% block content %}
  <p>Run a manual sync. Press the button to trigger a background sync. This will not block the web UI.</p>
  <div>
    <button id="run-sync-btn" onclick="triggerSync()">Run Sync Now</button>
    <span id="sync-state" style="margin-left:1rem;">Idle</span>
  </div>
  <script>
    async function triggerSync(){
      const btn = document.getElementById('run-sync-btn');
      btn.disabled = true;
      try {
        const res = await fetch('/sync', {method:'POST'});
        const js = await res.json();
        alert(js.message || 'Sync triggered');
      } catch (e) {
        alert('Failed to trigger sync: ' + e);
      } finally {
        setTimeout(()=>{ btn.disabled=false; }, 3000);
      }
    }
  </script>
{% endblock %}
EOF

# ---------------------------
# src/webui/templates/status.html
# ---------------------------
cat <<'EOF' > "src/webui/templates/status.html"
{% extends "layout.html" %}
{% block title %}Status{% endblock %}
{% block header %}Ebooks Manager Status{% endblock %}
{% block content %}
  <p>Last updated: {{ now }}</p>
  <h2>Sync State</h2>
  <p>Running: <strong>{{ sync_state.running }}</strong> — Last started: {{ sync_state.last_started }} — Last finished: {{ sync_state.last_finished }} — Last result: {{ sync_state.last_result }}</p>

  <h2>Sync History (Newest first)</h2>
  <table>
    <thead><tr><th>Timestamp</th><th>Action</th><th>Title/ID</th><th>Details</th><th>Status</th></tr></thead>
    <tbody>
      {% if history %}
        {% for entry in history %}
          <tr>
            <td>{{ entry.timestamp | strftime }}</td>
            <td>{{ entry.action }}</td>
            <td><strong>{{ entry.title or 'N/A' }}</strong><br><small>ID: <code>{{ entry.book_id or '' }}</code></small></td>
            <td>{% if entry.query %}Query: <code>{{ entry.query }}</code>{% endif %}</td>
            <td>{{ entry.status }}</td>
          </tr>
        {% endfor %}
      {% else %}
        <tr><td colspan="5">No history yet.</td></tr>
      {% endif %}
    </tbody>
  </table>

  <h2>Recent Logs</h2>
  <pre style="max-height:400px;overflow:auto;background:#222;color:#eee;padding:10px;border-radius:6px">{{ log_content or 'No logs available' }}</pre>
{% endblock %}
EOF

# ---------------------------
# src/webui/templates/shelf_view.html
# ---------------------------
cat <<'EOF' > "src/webui/templates/shelf_view.html"
{% extends "layout.html" %}
{% block title %}{{ shelf_name | capitalize }} Shelf{% endblock %}
{% block header %}{{ shelf_name | capitalize }} Shelf — {{ sync_status }}{% endblock %}
{% block content %}
  <table>
    <thead><tr><th>Title</th><th>Author</th><th>Goodreads ID</th><th>URL</th></tr></thead>
    <tbody>
      {% for book in books %}
        <tr>
          <td>{{ book.title }}</td>
          <td>{{ book.author }}</td>
          <td>{{ book.goodreads_id }}</td>
          <td><a href="{{ book.book_url }}" target="_blank">View</a></td>
        </tr>
      {% endfor %}
      {% if not books %}
        <tr><td colspan="4">No books found.</td></tr>
      {% endif %}
    </tbody>
  </table>
{% endblock %}
EOF

# ---------------------------
# src/job_runner.py (improved: safer scheduler)
# ---------------------------
cat <<'EOF' > "src/job_runner.py"
import logging
import os
import time
from apscheduler.schedulers.background import BackgroundScheduler
from src.sync_logic import orchestrate_sync
from src.utils.config_loader import load_config
from src.utils.logger import setup_logger

def run_jobs():
    config = load_config()
    setup_logger(config['log_file'], os.getenv('LOG_LEVEL', 'INFO'))
    logger = logging.getLogger(__name__)
    if os.getenv('SCHEDULER_STARTED', 'false') == 'true':
        logger.info("Scheduler already started. Exiting duplicate.")
        return
    os.environ['SCHEDULER_STARTED'] = 'true'
    scheduler = BackgroundScheduler()
    # daily run, can be customized via env vars in future
    scheduler.add_job(orchestrate_sync, 'interval', hours=24, next_run_time=None)
    scheduler.start()
    logger.info("Scheduler started.")
    try:
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()

if __name__ == "__main__":
    run_jobs()
EOF

# ---------------------------
# src/scrapers/__init__.py and other placeholders
# ---------------------------
cat <<'EOF' > "src/scrapers/__init__.py"
# scrapers package
EOF

cat <<'EOF' > "src/webui/__init__.py"
# webui package
EOF

cat <<'EOF' > "src/__init__.py"
# app package
EOF

# ---------------------------
# src/sync_logic.py was created earlier; ensure __init__ files exist
# ---------------------------
# done

# ---------------------------
# docker-compose.yml (unchanged functionality but ensure healthchecks)
# ---------------------------
cat <<'EOF' > "docker-compose.yml"
version: '3.8'
services:
  ebooks-manager:
    build:
      context: .
    container_name: ebooks-manager
    restart: unless-stopped
    env_file: /mnt/data/docker/docker-scripts/.env
    environment:
      - TZ=${TZ}
      - LOG_LEVEL=DEBUG
      - DRY_RUN=no
      - PYTHONUNBUFFERED=1
      - WEBPAGE_CACHE_DAYS=30
    volumes:
      - /mnt/data/docker/docker-config/ebooks-manager/config:/app/config
      - /mnt/data/docker/docker-config/ebooks-manager/logs:/app/logs
      - /mnt/data/docker/docker-config/ebooks-manager/data:/app/data
    shm_size: '4gb'
    ports:
      - "5002:5002"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5002/status || exit 1"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 30s

  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr:latest
    container_name: flaresolverr
    environment:
      - LOG_LEVEL=info
    ports:
      - "8191:8191"
    restart: unless-stopped
EOF

# ---------------------------
# Remove README creation per your request (do not create README or LICENSE)
# ---------------------------

# ---------------------------
# Final messages
# ---------------------------
echo "----------------------------------------------------"
echo "Project '$PROJECT_NAME' scaffolding created at: $ROOT_DIR"
echo "Key improvements included:"
echo " - Cookie-first login + fallback to requests session"
echo " - Improved and defensive Goodreads login (multiple selectors, 'Keep me signed in' attempt)"
echo " - More robust Selenium setup + graceful fallback if chromedriver missing"
echo " - Non-blocking web UI sync endpoint (/sync triggers background thread)"
echo " - /sync/status endpoint for UI to poll running status"
echo " - Better logging, full-page screenshots (debug), and safer bootstrap"
echo " - Removed README/LICENSE generation from this script (per request)"
echo ""
echo "Next steps:"
echo " 1) Copy config/config.json.template to /app/config/config.json with credentials or mount via docker-compose."
echo " 2) Adjust .env and docker-compose environment for your environment and start containers."
echo " 3) Run: docker build -t ebooks-manager . && docker-compose up -d --build"
echo "----------------------------------------------------"
