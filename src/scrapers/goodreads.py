# src/scrapers/goodreads.py
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
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

# Common cookie names that often indicate an authenticated session
LIKELY_SESSION_COOKIE_NAMES = [
    'session-id', 'sess', 's', 'auth_token', 'gr_user', 'cgcsess', 'session', 'csm-hit'
]

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
            LOG.info("Driver initialized successfully")
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
        """
        Save a screenshot and overlay the current URL at the top.
        If Pillow is unavailable or overlay fails, still save the raw screenshot.
        """
        if not self.driver or not (self.is_debug or force):
            return
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = self.screenshot_dir / f"{name}_{ts}.png"
            # try to set window size to full page height (best effort)
            try:
                height = self.driver.execute_script(
                    "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);"
                )
                width = self.driver.execute_script(
                    "return Math.max(document.body.scrollWidth, document.documentElement.scrollWidth);"
                )
                w = min(2400, int(width) if width else 1920)
                h = min(6000, int(height) if height else 1080)
                self.driver.set_window_size(w, h)
                time.sleep(0.2)
            except Exception:
                pass

            # save raw screenshot
            self.driver.save_screenshot(str(path))
            LOG.info("Saved screenshot: %s", path)

            # try overlaying URL using Pillow
            try:
                from PIL import Image, ImageDraw, ImageFont
                img = Image.open(path)
                draw = ImageDraw.Draw(img)
                try:
                    url_text = self.driver.current_url or ""
                except Exception:
                    url_text = ""
                bar_height = max(28, int(img.height * 0.04))
                # draw top bar
                draw.rectangle([(0, 0), (img.width, bar_height)], fill=(30, 30, 30))
                # font (defensive)
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size=max(12, bar_height - 10))
                except Exception:
                    font = ImageFont.load_default()
                # compute text height/width robustly
                display_text = url_text if len(url_text) <= 140 else ("..." + url_text[-137:])
                try:
                    # Pillow >= 8: textbbox available
                    bbox = draw.textbbox((0, 0), display_text, font=font)
                    text_w = bbox[2] - bbox[0]
                    text_h = bbox[3] - bbox[1]
                except Exception:
                    # fallback older method
                    try:
                        text_w, text_h = draw.textsize(display_text, font=font)
                    except Exception:
                        text_w, text_h = (0, bar_height - 6)
                padding = 8
                y = int((bar_height - text_h) / 2)
                draw.text((padding, y), display_text, fill=(255, 255, 255), font=font)
                # save overlay (overwrite)
                img.save(path)
                LOG.info("Saved screenshot with URL overlay: %s (url=%s)", path, url_text)
            except Exception as e:
                LOG.warning("URL overlay failed (Pillow missing or error): %s", e)
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
            # Navigate to base domain before adding cookies
            self.driver.get(self.base_url)
            for c in cookies:
                try:
                    c.pop('sameSite', None)
                    if 'expiry' in c and isinstance(c['expiry'], float):
                        c['expiry'] = int(c['expiry'])
                    if 'domain' in c and c['domain'].startswith('.'):
                        c['domain'] = c['domain'].lstrip('.')
                    self.driver.add_cookie(c)
                except Exception:
                    LOG.debug("Skipping invalid cookie: %s", c)
            LOG.info("Loaded cookies into selenium")
            return True
        except Exception as e:
            LOG.error("Failed to load cookies for selenium: %s", e, exc_info=True)
            return False

    def _load_cookies_into_requests(self):
        s = requests.Session()
        if not self.cookie_file.exists():
            return s
        try:
            with open(self.cookie_file, 'rb') as f:
                cookies = pickle.load(f)
            for c in cookies:
                domain = c.get('domain', '.goodreads.com')
                name = c.get('name')
                value = c.get('value')
                path = c.get('path', '/')
                if name and value:
                    s.cookies.set(name, value, domain=domain, path=path)
            LOG.info("Loaded cookies into requests session")
        except Exception as e:
            LOG.warning("Failed to load cookies into requests: %s", e)
        return s

    def _cookies_indicate_logged_in(self, cookies):
        for c in cookies:
            name = c.get('name') if isinstance(c, dict) else None
            if not name:
                continue
            if any(sn in name.lower() for sn in LIKELY_SESSION_COOKIE_NAMES):
                return True
        return False

    def _verify_logged_in_selenium(self):
        """
        Return True if any positive login indicator is present:
         - session cookie names
         - profile / signout selectors
         - 'My Books' link
         - absence of sign-in inputs/buttons
        """
        driver = self.driver
        try:
            # cookie check
            try:
                cookies = driver.get_cookies()
                if cookies and self._cookies_indicate_logged_in(cookies):
                    LOG.debug("Detected session cookie(s) after login flow")
                    return True
            except Exception:
                LOG.debug("Couldn't read cookies in verify step")

            selectors = [
                "a[href*='/user/sign_out']",
                "a[href*='/logout']",
                "a[href*='/user/show/']",
                "img.gravatar",
                "button[aria-label='Account menu']"
            ]
            for s in selectors:
                try:
                    el = driver.find_elements(By.CSS_SELECTOR, s)
                    if el and len(el) > 0:
                        LOG.debug("Found logged-in indicator element selector: %s", s)
                        return True
                except Exception:
                    continue

            # 'My Books' check
            try:
                if driver.find_elements(By.XPATH, "//a[contains(., 'My Books') or contains(., 'My books')]"):
                    LOG.debug("Found 'My Books' link (likely logged-in)")
                    return True
            except Exception:
                pass

            # absence of sign-in inputs/buttons
            signin_inputs = []
            try:
                signin_inputs = (
                    driver.find_elements(By.ID, "ap_email")
                    + driver.find_elements(By.NAME, "user[email]")
                    + driver.find_elements(By.XPATH, "//button[contains(., 'Sign in') or contains(., 'Sign-In')]")
                )
            except Exception:
                signin_inputs = []
            if not signin_inputs:
                LOG.debug("No sign-in inputs/buttons found on page; assume logged in")
                return True

        except Exception as e:
            LOG.debug("verify_logged_in_selenium encountered an error: %s", e, exc_info=True)
        return False

    def _wait_for_login_success(self, timeout=30, poll=0.5):
        start = time.time()
        while time.time() - start < timeout:
            try:
                if self._verify_logged_in_selenium():
                    LOG.info("Login verified by _wait_for_login_success")
                    return True
            except Exception:
                LOG.debug("Exception while checking login status", exc_info=True)
            time.sleep(poll)
        LOG.debug("Timed out waiting for login success")
        return False

    def login(self):
        if self.logged_in:
            LOG.info("Already logged in")
            return True

        driver = self._init_driver()
        if not driver:
            LOG.warning("Selenium not available; will attempt requests fallback using cookies.")
            return False

        # Try cookie restore
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
                    LOG.info("Saved cookies are invalid. Proceeding with full login.")
        except Exception as e:
            LOG.warning("Cookie-based login attempt failed: %s", e, exc_info=True)

        # Full login flow
        tries = 3
        for attempt in range(1, tries + 1):
            try:
                LOG.info("Navigating to Goodreads sign-in page.")
                driver.get(f"{self.base_url}/user/sign_in")
                self._take_screenshot("signin_portal_page")

                # Click sign-in button if present (Best-effort)
                try:
                    btn = WebDriverWait(driver, 6).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[contains(normalize-space(.),'Sign in with email') or contains(normalize-space(.),'Sign in with Amazon') or contains(normalize-space(.),'Sign in')]"))
                    )
                    try:
                        btn.click()
                        LOG.info("Clicked 'Sign in with email' button.")
                        time.sleep(0.8)
                    except Exception:
                        LOG.debug("Couldn't click sign-in button (will continue).")
                except TimeoutException:
                    LOG.debug("'Sign in with email' button not found, continue to locate inputs")

                # find credentials fields
                email_el = None
                password_el = None
                try:
                    email_el = WebDriverWait(driver, 8).until(
                        EC.presence_of_element_located((By.ID, "ap_email"))
                    )
                    password_el = driver.find_element(By.ID, "ap_password")
                    LOG.debug("Found Amazon ap_email/ap_password fields")
                except TimeoutException:
                    try:
                        email_el = WebDriverWait(driver, 6).until(
                            EC.presence_of_element_located((By.NAME, "user[email]"))
                        )
                        password_el = driver.find_element(By.NAME, "user[password]")
                        LOG.debug("Found Goodreads native fields")
                    except TimeoutException:
                        email_el = None
                        password_el = None

                if not email_el or not password_el:
                    self._take_screenshot(f"error_login_attempt_{attempt}", force=True)
                    raise Exception("Login form fields could not be located")

                # try "Keep me signed in"
                try:
                    for sel in [
                        (By.ID, "rememberMe"),
                        (By.NAME, "rememberMe"),
                        (By.XPATH, "//input[@type='checkbox' and (contains(@id,'remember') or contains(@name,'remember'))]"),
                        (By.XPATH, "//label[contains(., 'Keep me signed in')]/preceding-sibling::input[@type='checkbox']"),
                        (By.XPATH, "//span[contains(., 'Keep me signed in')]/preceding::input[1][@type='checkbox']")
                    ]:
                        try:
                            cb = driver.find_element(*sel)
                            if cb and not cb.is_selected():
                                cb.click()
                                LOG.info("Checked 'Keep me signed in' checkbox using selector %s", sel)
                                break
                        except Exception:
                            continue
                except Exception:
                    LOG.debug("Could not check 'Keep me signed in'")

                # enter credentials and submit
                LOG.info("Entering credentials (masked).")
                email_el.clear()
                email_el.send_keys(self.goodreads_email)
                password_el.clear()
                password_el.send_keys(self.goodreads_password)

                submitted = False
                for submit_selector in [
                    (By.ID, "signInSubmit"),
                    (By.XPATH, "//input[@type='submit' and (contains(@value,'Sign') or contains(@value,'sign'))]"),
                    (By.XPATH, "//button[contains(., 'Sign in') or contains(., 'Sign-In')]")
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
                    from selenium.webdriver.common.keys import Keys
                    try:
                        password_el.send_keys(Keys.RETURN)
                        LOG.debug("Submitted via Enter key")
                    except Exception:
                        pass

                success = self._wait_for_login_success(timeout=30)
                if success:
                    self._take_screenshot("login_success")
                    self._save_cookies()
                    self.logged_in = True
                    LOG.info("Successfully logged in (selenium)")
                    return True
                else:
                    self._take_screenshot(f"error_login_attempt_{attempt}", force=True)
                    raise Exception("Timed out waiting for login success indicators")

            except Exception as exc:
                LOG.warning("Login attempt %d failed: %s", attempt, exc, exc_info=True)
                time.sleep(2 + attempt * 2)
                if attempt == tries:
                    LOG.error("Exhausted login attempts")
                    break
                continue

        self._close_driver()
        return False

    def _fetch_shelf_with_requests(self, shelf_name):
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
        LOG.info("Fetching books from Goodreads '%s' shelf for user_id=%s...", shelf_name, self.user_id)
        page_source = None

        # Selenium path
        if self._selenium_available:
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

                    # Wait for the shelf table rows to appear (robust)
                    try:
                        WebDriverWait(self.driver, 20).until(
                            lambda d: d.find_elements(By.CSS_SELECTOR, "tbody#booksBody tr, tr.bookalike, table#books tr, td.field.title a")
                        )
                    except TimeoutException:
                        LOG.debug("Timeout while waiting for shelf table - will continue to parse whatever is available")

                    self._take_screenshot(f"fetch_shelf_{shelf_name}")
                    page_source = self.driver.page_source
                except Exception as e:
                    LOG.error("Selenium fetch of shelf failed: %s", e, exc_info=True)
                    self._take_screenshot("fetch_shelf_error", force=True)
                    page_source = None
            else:
                page_source = None

        # Requests fallback
        if not page_source:
            LOG.info("Attempting requests fallback to fetch shelf")
            page_source = self._fetch_shelf_with_requests(shelf_name)

        if not page_source:
            LOG.error("Unable to fetch shelf page via selenium or requests fallback")
            return []

        # Save raw HTML for debugging
        try:
            fname = self.cache_dir / f"shelf_{shelf_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            fname.write_text(page_source, encoding='utf-8')
            LOG.debug("Saved shelf HTML to %s", fname)
        except Exception:
            pass

        soup = BeautifulSoup(page_source, 'html.parser')
        books = []

        # Robust row selection - try several strongly-typed selectors in order
        candidate_rows = []
        selectors_tried = []
        try:
            # prefer explicit tbody id
            selectors_tried.append("tbody#booksBody tr")
            candidate_rows = soup.select("tbody#booksBody tr")
            if not candidate_rows:
                selectors_tried.append("tr.bookalike")
                candidate_rows = soup.select("tr.bookalike")
            if not candidate_rows:
                selectors_tried.append("table#books tr")
                candidate_rows = soup.select("table#books tr")
            if not candidate_rows:
                selectors_tried.append("table.table tr")
                candidate_rows = soup.select("table.table tr")
            if not candidate_rows:
                selectors_tried.append("td.field.title a")
                # fallback: find title anchors directly
                title_anchors = soup.select("td.field.title a, a.bookTitle, a.title")
                # map anchors back to rows
                for a in title_anchors:
                    tr = a.find_parent("tr")
                    if tr is not None:
                        candidate_rows.append(tr)
            LOG.debug("Selectors tried: %s ; candidate rows found: %d", selectors_tried, len(candidate_rows))
        except Exception:
            LOG.debug("Error when selecting candidate rows", exc_info=True)

        for row in candidate_rows:
            try:
                # Title and author live in td.field.title and td.field.author (per your HTML)
                title_elem = row.select_one('td.field.title a') or row.select_one('a.bookTitle') or row.select_one('a.title')
                author_elem = row.select_one('td.field.author a') or row.select_one('a.authorName') or row.select_one('.authorName')
                if not (title_elem and author_elem):
                    # some row formats may wrap differently; try finding anchors anywhere in row
                    title_elem = row.find('a', href=True, title=True) or title_elem
                    author_elem = row.find('a', href=True, text=True) or author_elem
                if not (title_elem and author_elem):
                    LOG.debug("Skipping row because title or author missing")
                    continue

                title = title_elem.get_text(strip=True)
                author = author_elem.get_text(strip=True)
                book_url = title_elem.get('href')
                goodreads_id = None
                if book_url:
                    parts = [p for p in book_url.split('/') if p]
                    for p in reversed(parts):
                        if p.isdigit():
                            goodreads_id = p
                            break
                # if still no numeric id, try data attributes or review id in tr id: e.g. id="review_7903565710"
                if not goodreads_id:
                    # check for data-resource-id or similar attributes on row or cover anchor
                    for attr in ('data-resource-id', 'data-book-id'):
                        if row.has_attr(attr):
                            goodreads_id = row[attr]
                            break
                    if not goodreads_id and row.has_attr('id'):
                        # extract digits
                        import re
                        m = re.search(r'(\d{6,})', row['id'])
                        if m:
                            goodreads_id = m.group(1)

                if not goodreads_id:
                    LOG.debug("Skipping book with no ID: %s", title)
                    continue

                books.append({
                    'goodreads_id': goodreads_id,
                    'title': title,
                    'author': author,
                    'book_url': urljoin(self.base_url, book_url) if book_url else None
                })
            except Exception:
                LOG.debug("Skipping malformed row", exc_info=True)
                continue

        LOG.info("Found %d books on shelf %s", len(books), shelf_name)
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
