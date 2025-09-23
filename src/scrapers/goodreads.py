# src/scrapers/goodreads.py
import os
import re
import time
import json
import pickle
import logging
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin, urlencode, urlparse, parse_qs

from bs4 import BeautifulSoup

# Selenium imports (used when available)
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, WebDriverException
except Exception:
    webdriver = None
    Service = None
    Options = None
    By = None
    WebDriverWait = None
    TimeoutException = None
    WebDriverException = None

try:
    from src.utils.database import Database
except Exception:
    # fallback to local import if running in different context
    from utils.database import Database

try:
    from src.api.cwa_client import CWAClient
except Exception:
    CWAClient = None

LOG = logging.getLogger(__name__)

# cookies that likely indicate logged-in state
LIKELY_SESSION_COOKIE_NAMES = ['session-id', 'sess', 's', 'auth_token', 'gr_user', 'cgcsess', 'session']

class GoodreadsScraper:
    def __init__(self, config: dict):
        """
        config: dictionary-like object, keys:
            - goodreads_user_id
            - cache_dir (optional, default /app/data/cache)
            - goodreads_per_page (optional, default 100)
            - chromedriver_path, chromium_bin, HEADLESS env handled
            - database_path (for Database init)
            - cwa_api_url/username/password (optional)
        """
        self.config = config or {}
        self.user_id = str(self.config.get('goodreads_user_id') or self.config.get('GOODREADS_USER_ID') or "")
        self.base_url = 'https://www.goodreads.com'
        cache_dir = Path(self.config.get('cache_dir', '/app/data/cache'))
        self.cache_dir = cache_dir
        self.cookie_file = cache_dir / 'logins' / 'goodreads' / 'cookies.pkl'
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        self.cover_cache = cache_dir / 'covers'
        self.cover_cache.mkdir(parents=True, exist_ok=True)

        # per_page configurable; allow string or int; default = 100
        try:
            self.per_page = int(self.config.get('goodreads_per_page', self.config.get('GOODREADS_PER_PAGE', 100)))
        except Exception:
            self.per_page = 100

        # WebDriver settings
        self.driver = None
        self._selenium_available = bool(webdriver and Options and Service)
        self._init_selenium_settings()

        # DB and optional CWA client
        db_path = self.config.get('database_path') or self.config.get('DATABASE_PATH') or '/app/data/databases/goodreads.db'
        self.db = Database(db_path)
        self.cwa_client = None
        if CWAClient and (self.config.get('cwa_api_url') or os.getenv('CWA_API_URL')):
            try:
                self.cwa_client = CWAClient(self.config.get('cwa_api_url'), self.config.get('cwa_username'), self.config.get('cwa_password'), str(self.cache_dir))
            except Exception:
                LOG.exception("Failed to init CWA client")

        LOG.info("GoodreadsScraper initialized user=%s per_page=%d", self.user_id, self.per_page)

    def _init_selenium_settings(self):
        # only set these here; actual driver init in _init_driver
        self.headless = os.getenv('HEADLESS', '1') == '1'
        self.chromium_bin = os.getenv('CHROMIUM_BIN', '/usr/bin/chromium')
        self.chromedriver_path = os.getenv('CHROMEDRIVER_PATH', '/usr/bin/chromedriver')

    def _init_driver(self):
        if self.driver:
            return self.driver
        if not self._selenium_available:
            LOG.info("Selenium not available in environment")
            return None
        try:
            options = Options()
            if self.headless:
                # newer chrome uses '--headless=new'
                try:
                    options.add_argument('--headless=new')
                except Exception:
                    options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1600,1200')
            ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36"
            options.add_argument(f'user-agent={ua}')
            if Path(self.chromium_bin).exists():
                options.binary_location = self.chromium_bin
            if not Path(self.chromedriver_path).exists():
                LOG.warning("Chromedriver not found at %s - Selenium disabled", self.chromedriver_path)
                self._selenium_available = False
                return None
            service = Service(executable_path=self.chromedriver_path)
            self.driver = webdriver.Chrome(service=service, options=options)
            return self.driver
        except WebDriverException:
            LOG.exception("Failed to init Selenium driver")
            self._selenium_available = False
            return None
        except Exception:
            LOG.exception("Selenium init error")
            self._selenium_available = False
            return None

    def _close_driver(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
        self.driver = None

    def _load_cookies_into_requests(self):
        s = requests.Session()
        if not self.cookie_file.exists():
            return s
        try:
            with open(self.cookie_file, 'rb') as f:
                cookies = pickle.load(f)
            for c in cookies:
                name = c.get('name')
                value = c.get('value')
                domain = c.get('domain', '.goodreads.com')
                path = c.get('path', '/')
                if name and value:
                    s.cookies.set(name, value, domain=domain, path=path)
            LOG.debug("Loaded cookies into requests session")
        except Exception:
            LOG.exception("Failed to load cookies into requests session")
        return s

    def _save_cookies_from_selenium(self):
        if not self.driver:
            return
        try:
            cookies = self.driver.get_cookies()
            with open(self.cookie_file, 'wb') as f:
                pickle.dump(cookies, f)
            LOG.debug("Saved selenium cookies")
        except Exception:
            LOG.exception("Failed to save selenium cookies")

    def _cookies_indicate_logged_in(self, cookies_list):
        for c in cookies_list:
            n = c.get('name')
            if not n:
                continue
            if any(k in n.lower() for k in LIKELY_SESSION_COOKIE_NAMES):
                return True
        return False

    def login_selenium_with_cookies(self):
        drv = self._init_driver()
        if not drv:
            return False
        try:
            drv.get(self.base_url)
            if self.cookie_file.exists():
                with open(self.cookie_file, 'rb') as f:
                    cookies = pickle.load(f)
                for c in cookies:
                    try:
                        c.pop('sameSite', None)
                        if 'expiry' in c and isinstance(c['expiry'], float):
                            c['expiry'] = int(c['expiry'])
                        if 'domain' in c and c['domain'].startswith('.'):
                            c['domain'] = c['domain'].lstrip('.')
                        drv.add_cookie(c)
                    except Exception:
                        continue
                drv.refresh()
                time.sleep(1)
                try:
                    if self._cookies_indicate_logged_in(drv.get_cookies()):
                        LOG.info("Logged in via cookies (selenium)")
                        return True
                except Exception:
                    pass
        except Exception:
            LOG.exception("Cookie-based selenium login failed")
        return False

    def ensure_all_columns_visible(self):
        """If the shelf settings control is present, click it, enable all checkboxes, set per_page to configured value and save."""
        if not self.driver:
            return
        try:
            # Click the settings link if present
            try:
                link = self.driver.find_element(By.ID, "shelfSettingsLink")
                try:
                    link.click()
                    time.sleep(0.6)
                except Exception:
                    self.driver.execute_script("arguments[0].click();", link)
                    time.sleep(0.6)
            except Exception:
                # settings link not present - ignore
                pass

            # now in settings, check all checkboxes inside #shelfSettings
            try:
                settings = self.driver.find_element(By.ID, "shelfSettings")
                # check all input[type=checkbox] within settings
                checkboxes = settings.find_elements(By.CSS_SELECTOR, "input[type=checkbox]")
                for cb in checkboxes:
                    try:
                        if not cb.is_selected():
                            self.driver.execute_script("arguments[0].click();", cb)
                    except Exception:
                        continue
                # set per_page select to self.per_page if present
                try:
                    sel = settings.find_element(By.ID, "user_shelf_per_page")
                    # set via JS
                    self.driver.execute_script(f"arguments[0].value = '{self.per_page}'; arguments[0].dispatchEvent(new Event('change'))", sel)
                except Exception:
                    pass

                # click Save if present
                try:
                    save_btn = settings.find_element(By.CSS_SELECTOR, "input[type=submit][id='save_curr_sett_submit']")
                    if save_btn:
                        self.driver.execute_script("arguments[0].click()", save_btn)
                        time.sleep(0.8)
                except Exception:
                    # maybe no save button
                    pass
            except Exception:
                # no settings element
                pass
        except Exception:
            LOG.exception("Failed to ensure all columns visible")

    def _download_cover(self, url, goodreads_id):
        """Download cover image and return local path or None."""
        if not url:
            return None
        try:
            parsed = urlparse(url)
            ext = Path(parsed.path).suffix or ".jpg"
            fname = f"{goodreads_id}{ext}"
            local = self.cover_cache / fname
            if local.exists():
                return str(local.name)
            resp = requests.get(url, timeout=20, stream=True)
            if resp.status_code == 200:
                with open(local, "wb") as f:
                    for chunk in resp.iter_content(1024 * 8):
                        f.write(chunk)
                return str(local.name)
        except Exception:
            LOG.exception("Failed to download cover: %s", url)
        return None

    def _parse_series_from_title(self, title):
        """
        Try to parse series info from title text.
        Returns (title_clean, series_name, series_number, series_id)
        """
        if not title:
            return title, None, None, None
        t = title.strip()
        series_name = None
        series_number = None
        series_id = None

        # find parentheses content at end
        m = re.search(r'\(([^()]+)\)\s*$', t)
        if m:
            inside = m.group(1)
            mnum = re.search(r'#\s*([\d\.]+)', inside)
            if mnum:
                series_number = mnum.group(1)
            cleaned = re.sub(r',?\s*#\s*[\d\.]+', '', inside).strip()
            series_name = cleaned if cleaned else None
            title_clean = re.sub(r'\s*\([^()]*\)\s*$', '', t).strip()
            return title_clean, series_name, series_number, None

        m2 = re.search(r'(.*?)\s+[-–/]\s*(.+?#\s*[\d\.]+)\s*$', t)
        if m2:
            title_clean = m2.group(1).strip()
            inside = m2.group(2)
            mnum = re.search(r'#\s*([\d\.]+)', inside)
            if mnum:
                series_number = mnum.group(1)
            series_name = re.sub(r',?\s*#\s*[\d\.]+', '', inside).strip()
            return title_clean, series_name, series_number, None

        return t, None, None, None

    def _normalize_author_first(self, author_text):
        """
        Normalize author into 'First Last' form.
        Examples:
           "Eatough, Nicole" -> "Nicole Eatough"
        """
        if not author_text:
            return None
        a = author_text.strip()
        if ',' in a:
            parts = [p.strip() for p in a.split(',') if p.strip()]
            if len(parts) >= 2:
                return " ".join(parts[::-1])
        return a

    def _map_row_by_headers(self, headers, row):
        """
        headers: list of header keys (in order)
        row: BeautifulSoup tr element
        Return dict mapping keys to extracted cell values
        """
        cells = row.find_all(['td', 'th'])
        data = {}
        for idx, h in enumerate(headers):
            key = h or f'col{idx}'
            val = None
            if idx < len(cells):
                cell = cells[idx]
                a = cell.find('a')
                if a and a.get('href') and (key in ('title', 'cover', 'author') or a.get_text(strip=True)):
                    if key == 'title':
                        val = a.get_text(" ", strip=True)
                        href = a.get('href')
                        data['book_url'] = urljoin(self.base_url, href) if href else None
                    elif key == 'author':
                        val = a.get_text(" ", strip=True)
                    elif key == 'cover':
                        img = cell.find('img')
                        if img and img.get('src'):
                            val = img.get('src')
                        else:
                            val = a.get_text(" ", strip=True)
                    else:
                        val = a.get_text(" ", strip=True)
                else:
                    val = cell.get_text(" ", strip=True)
                    if key == 'cover':
                        img = cell.find('img')
                        if img and img.get('src'):
                            val = img.get('src')
                if val:
                    if key in ('position', 'num_pages', 'num_ratings', 'comments', 'votes', 'read_count'):
                        m = re.search(r'(\d+)', val.replace(',', ''))
                        if m:
                            try:
                                val = int(m.group(1))
                            except:
                                pass
                    if key in ('avg_rating',):
                        m = re.search(r'([0-9]+(\.[0-9]+)?)', val)
                        if m:
                            try:
                                val = float(m.group(1))
                            except:
                                pass
            data[key] = val
        return data

    def get_goodreads_books_from_shelf(self, shelf_name='to-download', fetch_details=False, max_pages=50):
        """
        Fetch books using the 'ALL' shelf view with pagination using per_page configured.
        Returns list of book dicts.
        """
        LOG.info("Fetching books from Goodreads shelf='%s' user=%s per_page=%d", shelf_name, self.user_id, self.per_page)

        if not self.user_id:
            raise ValueError("goodreads_user_id not set in config")

        collected = []
        page_index = 1

        # Try Selenium first, better at toggling settings
        drv = None
        if self._selenium_available:
            drv = self._init_driver()
        if drv:
            try:
                try:
                    self.login_selenium_with_cookies()
                except Exception:
                    pass

                base_shelf = f"{self.base_url}/review/list/{self.user_id}?utf8=✓&shelf=%23ALL%23&per_page={self.per_page}"
                while page_index <= max_pages:
                    url = f"{base_shelf}&page={page_index}"
                    LOG.info("Selenium navigating to shelf page %s", url)
                    drv.get(url)
                    time.sleep(1.0)
                    self.ensure_all_columns_visible()
                    try:
                        WebDriverWait(drv, 6).until(
                            lambda d: d.find_elements(By.CSS_SELECTOR, "tbody#booksBody tr, tr.bookalike, table#books tr")
                        )
                    except Exception:
                        LOG.debug("Timed out waiting for shelf table; parsing whatever is present")
                    html = drv.page_source
                    page_books, has_next = self._parse_shelf_html_and_save(html, shelf_name, fetch_details)
                    collected.extend(page_books)
                    LOG.info("Page %d: collected %d books (cumulative %d)", page_index, len(page_books), len(collected))
                    if not has_next:
                        break
                    page_index += 1
            except Exception:
                LOG.exception("Selenium shelf fetch failed; falling back to requests")
            finally:
                try:
                    self._save_cookies_from_selenium()
                finally:
                    self._close_driver()

        # If Selenium not available or returned nothing, fallback to requests
        if not collected:
            try:
                session = self._load_cookies_into_requests()
                base_shelf = f"{self.base_url}/review/list/{self.user_id}"
                params = {'utf8': '✓', 'shelf': '#ALL#', 'per_page': str(self.per_page)}
                page_index = 1
                while page_index <= max_pages:
                    params['page'] = str(page_index)
                    full = f"{base_shelf}?{urlencode(params)}"
                    LOG.info("Requests fetching %s", full)
                    r = session.get(full, timeout=30)
                    if r.status_code != 200:
                        LOG.warning("Requests fetch returned %s", r.status_code)
                        break
                    page_books, has_next = self._parse_shelf_html_and_save(r.text, shelf_name, fetch_details)
                    collected.extend(page_books)
                    LOG.info("Page %d: collected %d books (cumulative %d)", page_index, len(page_books), len(collected))
                    if not has_next:
                        break
                    page_index += 1
            except Exception:
                LOG.exception("Requests shelf fetch failed")

        LOG.info("Total books collected from shelf view: %d", len(collected))
        return collected

    def _parse_shelf_html_and_save(self, html, shelf_name, fetch_details=False):
        """
        Parse HTML of a shelf page and store rows into DB.
        Returns (books_list, has_next_page)
        """
        soup = BeautifulSoup(html, 'html.parser')
        # detect pagination next link
        has_next = False
        try:
            next_link = soup.select_one('a.next_page')
            if next_link and ('disabled' not in (next_link.get('class') or [])):
                has_next = True
            # Goodreads sometimes uses rel=next anchor
            if not has_next:
                rel_next = soup.find('a', rel='next')
                if rel_next:
                    has_next = True
        except Exception:
            has_next = False

        # detect headers in table#books
        headers = []
        header_row = soup.select_one('table#books thead tr') or soup.select_one('thead tr')
        if header_row:
            for th in header_row.find_all('th'):
                alt = th.get('alt') or th.get('data-field') or (th.get('class') and " ".join(th.get('class'))) or th.get_text(" ", strip=True)
                raw = (alt or '').strip().lower()
                if 'title' in raw:
                    headers.append('title')
                elif 'author' in raw:
                    headers.append('author')
                elif 'cover' in raw:
                    headers.append('cover')
                elif 'isbn13' in raw:
                    headers.append('isbn13')
                elif 'isbn' in raw:
                    headers.append('isbn')
                elif 'asin' in raw:
                    headers.append('asin')
                elif 'avg' in raw and 'rating' in raw:
                    headers.append('avg_rating')
                elif 'num_ratings' in raw or 'num ratings' in raw:
                    headers.append('num_ratings')
                elif 'date_pub' in raw or 'date pub' in raw:
                    headers.append('date_pub')
                elif 'date_pub_edition' in raw or 'edition' in raw:
                    headers.append('date_pub_edition')
                elif 'num_pages' in raw or 'num pages' in raw:
                    headers.append('num_pages')
                elif raw.strip() in ('rating','my rating'):
                    headers.append('rating')
                elif 'shelf' in raw or 'shelves' in raw:
                    headers.append('shelves')
                elif 'date_added' in raw or 'added' in raw:
                    headers.append('date_added')
                elif 'date_read' in raw:
                    headers.append('date_read')
                elif 'date_started' in raw:
                    headers.append('date_started')
                elif 'review' in raw:
                    headers.append('review')
                elif 'notes' in raw:
                    headers.append('notes')
                elif 'comments' in raw:
                    headers.append('comments')
                elif 'votes' in raw:
                    headers.append('votes')
                elif 'position' in raw:
                    headers.append('position')
                elif 'owned' in raw:
                    headers.append('owned')
                elif 'format' in raw:
                    headers.append('format')
                elif 'actions' in raw:
                    headers.append('actions')
                elif 'recommender' in raw:
                    headers.append('recommender')
                elif 'read_count' in raw or 'read count' in raw:
                    headers.append('read_count')
                else:
                    token = re.sub(r'\W+', '_', raw).strip('_')
                    headers.append(token or 'col')
        else:
            # fallback guess
            headers = ['position','cover','title','author','isbn','avg_rating','num_ratings','date_pub','rating','shelves','review','notes','comments','votes','date_read','date_added','format','actions']

        books = []
        rows = soup.select('tbody#booksBody tr') or soup.select('tr.bookalike') or []
        for row in rows:
            try:
                mapped = self._map_row_by_headers(headers, row)
                # find goodreads id
                gid = None
                if mapped.get('book_url'):
                    m = re.search(r'/book/show/(\d+)', mapped.get('book_url'))
                    if m:
                        gid = m.group(1)
                    else:
                        m2 = re.search(r'(\d{6,})', mapped.get('book_url'))
                        if m2:
                            gid = m2.group(1)
                if not gid:
                    if row.has_attr('id'):
                        m3 = re.search(r'(\d{6,})', row['id'])
                        if m3:
                            gid = m3.group(1)
                if not gid:
                    for attr in ('data-resource-id','data-book-id','data-review-id'):
                        if row.has_attr(attr):
                            gid = row[attr]
                            break
                if not gid:
                    LOG.debug("Skipping row without goodreads id (title=%s)", mapped.get('title'))
                    continue

                title_raw = mapped.get('title') or None
                title_clean, series_name, series_number, series_id = self._parse_series_from_title(title_raw)
                author = mapped.get('author') or None
                author_first = self._normalize_author_first(author)

                # cover handling
                cover_url = mapped.get('cover') or None
                cover_local = None
                try:
                    cover_local = self._download_cover(cover_url, gid) if cover_url else None
                except Exception:
                    cover_local = None

                shelves_val = mapped.get('shelves')
                if shelves_val:
                    shelves = ", ".join([s.strip() for s in re.split(r'[,/]|\\|;', shelves_val) if s.strip()])
                else:
                    shelves = shelf_name

                book = {
                    'goodreads_id': str(gid),
                    'title': title_raw,
                    'title_clean': title_clean,
                    'author': author,
                    'author_first': author_first,
                    'series_name': series_name,
                    'series_number': series_number,
                    'series_id': series_id,
                    'position': mapped.get('position'),
                    'cover_url': cover_url,
                    'cover_local_path': cover_local,  # store filename or None
                    'book_url': mapped.get('book_url'),
                    'isbn': mapped.get('isbn'),
                    'isbn13': mapped.get('isbn13'),
                    'asin': mapped.get('asin'),
                    'avg_rating': mapped.get('avg_rating'),
                    'num_ratings': mapped.get('num_ratings'),
                    'date_pub': mapped.get('date_pub'),
                    'date_pub_edition': mapped.get('date_pub_edition'),
                    'num_pages': mapped.get('num_pages'),
                    'rating': mapped.get('rating'),
                    'shelves': shelves,
                    'review': mapped.get('review'),
                    'notes': mapped.get('notes'),
                    'comments': mapped.get('comments'),
                    'votes': mapped.get('votes'),
                    'date_read': mapped.get('date_read'),
                    'date_started': mapped.get('date_started'),
                    'date_added': mapped.get('date_added'),
                    'date_purchased': mapped.get('date_purchased'),
                    'purchase_location': mapped.get('purchase_location'),
                    'owned': mapped.get('owned'),
                    'condition': mapped.get('condition'),
                    'format': mapped.get('format'),
                    'recommender': mapped.get('recommender'),
                    'read_count': mapped.get('read_count'),
                    'genres': None,
                    'fetched_at': datetime.utcnow().isoformat()
                }

                # Save to DB. Use db.save_book (should properly insert/update)
                try:
                    self.db.save_book(book, shelves=[shelf_name])
                except Exception:
                    LOG.exception("Failed to save book to DB: %s", gid)

                # Add history (if DB supports)
                try:
                    self.db.add_history(action='fetch_shelf_row', book_id=gid, title=title_clean, status='fetched', meta={'shelf': shelf_name})
                except Exception:
                    # not fatal
                    pass

                books.append(book)
            except Exception:
                LOG.exception("Failed to parse/save row")
                continue

        return books, has_next
