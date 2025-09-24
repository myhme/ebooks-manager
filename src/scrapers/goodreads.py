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

# Selenium imports (used and required)
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
    EC = None
    TimeoutException = None
    WebDriverException = None

try:
    from src.utils.database import Database
except Exception:
    # fallback to relative import if run in different context
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

        LOG.info("GoodreadsScraper initialized user=%s per_page=%d selenium=%s", self.user_id, self.per_page, self._selenium_available)

        # Force Selenium available per your request
        if not self._selenium_available:
            raise RuntimeError("Selenium is required but not available in this environment. Please install Selenium and the chromedriver binary.")

    def _init_selenium_settings(self):
        # only set these here; actual driver init in _init_driver
        self.headless = os.getenv('HEADLESS', '1') == '1'
        self.chromium_bin = os.getenv('CHROMIUM_BIN', '/usr/bin/chromium')
        self.chromedriver_path = os.getenv('CHROMEDRIVER_PATH', '/usr/bin/chromedriver')

    def _init_driver(self):
        if self.driver:
            return self.driver
        if not self._selenium_available:
            LOG.error("Selenium not available")
            return None
        try:
            options = Options()
            if self.headless:
                # newer chrome uses '--headless=new' possibly
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
        """
        If the shelf settings control is present, open it (if necessary),
        check all column checkboxes except 'position', set per_page to configured value and save.
        This tries to make the table include the full set of columns so requests parsing can see them.
        """
        if not self.driver:
            LOG.debug("No driver for ensure_all_columns_visible")
            return
        try:
            # If the settings link exists, click to open
            try:
                link = self.driver.find_element(By.ID, "shelfSettingsLink")
                try:
                    if not link.is_displayed():
                        # try JS click anyway
                        self.driver.execute_script("arguments[0].click();", link)
                    else:
                        link.click()
                    time.sleep(0.4)
                except Exception:
                    self.driver.execute_script("arguments[0].click();", link)
                    time.sleep(0.4)
            except Exception:
                # If there is no link, maybe settings are already visible. continue
                pass

            # Wait for settings element (short)
            try:
                WebDriverWait(self.driver, 3).until(
                    lambda d: d.find_element(By.ID, "shelfSettings")
                )
            except Exception:
                # not present - maybe settings not available; continue gracefully
                pass

            # Find settings container
            try:
                settings = self.driver.find_element(By.ID, "shelfSettings")
            except Exception:
                settings = None

            if settings:
                # Check all input[type=checkbox] inside settings except position
                try:
                    checkboxes = settings.find_elements(By.CSS_SELECTOR, "input[type=checkbox]")
                    for cb in checkboxes:
                        try:
                            alt = cb.get_attribute("alt") or cb.get_attribute("name") or cb.get_attribute("id") or ""
                            alt_l = alt.lower()
                            # Skip position checkbox
                            if 'position' in alt_l:
                                continue
                            # If not selected, click via JS to be robust
                            if not cb.is_selected():
                                self.driver.execute_script("arguments[0].click();", cb)
                                time.sleep(0.05)
                        except Exception:
                            continue
                except Exception:
                    LOG.debug("Failed to select checkboxes in settings")

                # set per_page select to self.per_page if present
                try:
                    sel = settings.find_element(By.ID, "user_shelf_per_page")
                    # set via JS and dispatch change
                    try:
                        self.driver.execute_script("arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('change'))", sel, str(self.per_page))
                    except Exception:
                        # fallback: try selecting using option
                        for opt in sel.find_elements(By.TAG_NAME, "option"):
                            if opt.get_attribute("value") == str(self.per_page):
                                opt.click()
                                break
                except Exception:
                    LOG.debug("Per-page select not found in settings")

                # click Save if present
                try:
                    save_btn = settings.find_element(By.CSS_SELECTOR, "input[type=submit]#save_curr_sett_submit")
                    if save_btn:
                        try:
                            self.driver.execute_script("arguments[0].click()", save_btn)
                        except Exception:
                            try:
                                save_btn.click()
                            except Exception:
                                pass
                        # allow Goodreads a moment to persist settings
                        time.sleep(1.0)
                except Exception:
                    # maybe no save button
                    pass

            # ensure settings closed or stable
            time.sleep(0.4)
        except Exception:
            LOG.exception("Failed to ensure all columns visible")

    def _download_cover(self, url, goodreads_id):
        """Download cover image and return local filename or None."""
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
                    # handle images in cover cell
                    img = cell.find('img')
                    if key == 'cover' and img and img.get('src'):
                        val = img.get('src')
                    else:
                        val = cell.get_text(" ", strip=True)
                if val:
                    if key in ('position', 'num_pages', 'num_ratings', 'comments', 'votes', 'read_count'):
                        m = re.search(r'(\d+)', val.replace(',', ''))
                        if m:
                            try:
                                val = int(m.group(1))
                            except Exception:
                                pass
                    if key in ('avg_rating',):
                        m = re.search(r'([0-9]+(\.[0-9]+)?)', val)
                        if m:
                            try:
                                val = float(m.group(1))
                            except Exception:
                                pass
            data[key] = val
        return data

    def get_goodreads_books_from_shelf(self, shelf_name='all', fetch_details=False, max_pages=200):
        """
        Fetch books using the 'ALL' shelf view with pagination using per_page configured.
        Uses Selenium (forced) for correct settings and ensures all columns / shelves anchors are visible.
        Returns list of book dicts for the requested shelf.
        If shelf_name != 'all', results are filtered in Python from the ALL view.
        """
        LOG.info("Fetching books from Goodreads shelf='%s' user=%s per_page=%d",
                 shelf_name, self.user_id, self.per_page)

        if not self.user_id:
            raise ValueError("goodreads_user_id not set in config")

        collected = []
        page_index = 1
        total_expected = None

        drv = self._init_driver()
        if not drv:
            raise RuntimeError("Selenium driver could not be started")

        try:
            try:
                self.login_selenium_with_cookies()
            except Exception:
                LOG.debug("cookie login attempt failed")

            base_shelf = f"{self.base_url}/review/list/{self.user_id}?utf8=✓&shelf=%23ALL%23&per_page={self.per_page}"

            while page_index <= max_pages:
                url = f"{base_shelf}&page={page_index}"
                LOG.info("Selenium navigating to shelf page %s", url)
                drv.get(url)
                time.sleep(0.8)

                try:
                    self.ensure_all_columns_visible()
                except Exception:
                    LOG.debug("ensure_all_columns_visible encountered an issue")

                try:
                    el = drv.find_element(By.CSS_SELECTOR, "a.selectedShelf")
                    if el:
                        txt = el.text or ""
                        m = re.search(r'All\s*\((\d+)\)', txt)
                        if m:
                            total_expected = int(m.group(1))
                            LOG.debug("Total expected books detected: %d", total_expected)
                except Exception:
                    pass

                try:
                    WebDriverWait(drv, 6).until(
                        lambda d: len(d.find_elements(
                            By.CSS_SELECTOR, "tbody#booksBody tr, tr.bookalike, table#books tr")) > 0
                    )
                except Exception:
                    LOG.debug("Timed out waiting for shelf table; parsing whatever is present")

                html = drv.page_source
                page_books, has_next = self._parse_shelf_html_and_save(html, 'all', fetch_details)
                collected.extend(page_books)
                LOG.info("Page %d: collected %d books (cumulative %d)",
                         page_index, len(page_books), len(collected))

                if total_expected and len(collected) >= total_expected:
                    LOG.info("Reached expected total (%d) after page %d", total_expected, page_index)
                    break

                if not has_next:
                    LOG.debug("No next page detected; stopping at page %d", page_index)
                    break

                page_index += 1

            try:
                self._save_cookies_from_selenium()
            except Exception:
                LOG.debug("Saving cookies failed")
        except Exception:
            LOG.exception("Selenium shelf fetch failed")
        finally:
            try:
                self._close_driver()
            except Exception:
                pass

        # If the user requested a specific shelf, filter from collected ALL-books
        if shelf_name and shelf_name.lower() != 'all':
            filtered = [b for b in collected
                        if b.get('shelves') and shelf_name.lower() in b.get('shelves').lower()]
            LOG.info("Filtered %d books for shelf='%s' from %d total",
                     len(filtered), shelf_name, len(collected))
            return filtered

        LOG.info("Total books collected from ALL shelves: %d (expected=%s)",
                 len(collected), total_expected)
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
            if next_link and ('disabled' not in (next_link.get('class') or [])) and next_link.get('href'):
                has_next = True
            # Goodreads sometimes uses rel=next anchor
            if not has_next:
                rel_next = soup.find('a', rel='next')
                if rel_next and rel_next.get('href'):
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
        # try to select rows: prefer tbody#booksBody's tr if present
        rows = soup.select('tbody#booksBody tr') or soup.select('tr.bookalike') or []
        for row in rows:
            try:
                mapped = self._map_row_by_headers(headers, row)
                # find goodreads id
                gid = None
                if mapped.get('book_url'):
                    m = re.search(r'/book/show/(\d+)', mapped.get('book_url') or "")
                    if m:
                        gid = m.group(1)
                    else:
                        m2 = re.search(r'(\d{6,})', mapped.get('book_url') or "")
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

                # shelves: prefer anchor links with class shelfLink (captures exact shelf names)
                shelves_list = []
                try:
                    # search for anchors inside the shelves cell
                    shelves_cell = None
                    # try to find shelves cell by header position if headers list contains 'shelves'
                    if 'shelves' in headers:
                        idx = headers.index('shelves')
                        cells = row.find_all(['td', 'th'])
                        if idx < len(cells):
                            shelves_cell = cells[idx]
                    # fallback: try to locate by class/selector
                    if not shelves_cell:
                        shelves_cell = row.select_one('.field.shelves, td.field.shelves, td.shelves, .shelfList, .shelfLink') or row

                    if shelves_cell:
                        # find shelfLink anchors
                        for a in shelves_cell.find_all('a', class_='shelfLink'):
                            text = (a.get_text(" ", strip=True) or "").strip()
                            if text:
                                # anchor may show "read" or "read (something)" — keep the shelf token before any whitespace/paren
                                # but usually shelf names are simple (e.g., "to-read", "to-download", "owned")
                                shelves_list.append(text)
                        # if none found, perhaps the cell has plain text separated by commas
                        if not shelves_list:
                            raw_shelves = shelves_cell.get_text(" ", strip=True)
                            if raw_shelves:
                                parts = [s.strip() for s in re.split(r'[,/\\;]|(?:\s+and\s+)', raw_shelves) if s.strip()]
                                shelves_list.extend(parts)
                except Exception:
                    LOG.debug("Failed to parse shelves from row")

                # fallback: if still nothing, use the requested shelf_name
                if not shelves_list:
                    shelves_list = [shelf_name]

                # normalize shelf names: remove excess text like counts etc.
                norm_shelves = []
                for s in shelves_list:
                    # strip counts e.g. "All (251)" or "read (12)" -> take first token before '('
                    s2 = re.sub(r'\s*\(.*?\)\s*', '', s).strip()
                    # sometimes shelf anchor includes user's name or text; keep the token
                    if s2:
                        norm_shelves.append(s2)
                if not norm_shelves:
                    norm_shelves = [shelf_name]

                # join as comma string for DB
                shelves_str = ", ".join(norm_shelves)

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
                    'shelves': shelves_str,
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

                # Save to DB. Use db.save_book with explicit shelves list so DB stores proper shelves.
                try:
                    # pass normalized shelf names so DB.save_book sees them explicitly
                    self.db.save_book(book, shelves=norm_shelves)
                except Exception:
                    LOG.exception("Failed to save book to DB: %s", gid)

                # Add history (if DB supports)
                try:
                    self.db.add_history(action='fetch_shelf_row', book_id=gid, title=title_clean, status='fetched', meta={'shelves': norm_shelves})
                except Exception:
                    # not fatal
                    pass

                books.append(book)
            except Exception:
                LOG.exception("Failed to parse/save row")
                continue

        return books, has_next
