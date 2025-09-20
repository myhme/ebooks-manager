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
