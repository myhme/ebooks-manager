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
from typing import List, Optional, Dict, Any, Tuple
from urllib.parse import urljoin, urlparse

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
    from utils.database import Database  # fallback for different execution context

try:
    from src.api.cwa_client import CWAClient
except Exception:
    CWAClient = None

LOG = logging.getLogger(__name__)

# cookies that likely indicate logged-in state
LIKELY_SESSION_COOKIE_NAMES = ['session-id', 'sess', 's', 'auth_token', 'gr_user', 'cgcsess', 'session']


BOOK_COLUMNS = [
    "goodreads_id", "title", "title_clean", "author", "author_first",
    "series_name", "series_number", "series_id", "pub_date", "pub_date_edition",
    "num_pages", "isbn", "isbn13", "asin", "language",
    "genres", "json_details", "position", "cover_url", "cover_local_path",
    "book_url", "avg_rating", "num_ratings", "rating", "shelves",
    "review", "notes", "comments", "votes", "date_read",
    "date_started", "date_added", "date_purchased", "purchase_location", "owned",
    "condition", "format", "recommender", "read_count", "cover_downloaded",
    "last_synced", "fetched_at"
]


class GoodreadsScraper:
    def __init__(self, config: dict):
        """
        config keys expected:
            - goodreads_user_id
            - cache_dir (default /app/data/cache)
            - goodreads_per_page (default 100)
            - CHROMEDRIVER_PATH / CHROMIUM_BIN / HEADLESS via env handled
            - database_path (optional)
            - cwa_api_url / cwa_username / cwa_password (optional)
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

        try:
            self.per_page = int(self.config.get('goodreads_per_page', self.config.get('GOODREADS_PER_PAGE', 100)))
        except Exception:
            self.per_page = 100

        # Selenium settings
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

        # The environment you requested requires Selenium. If not available, raise.
        if not self._selenium_available:
            raise RuntimeError("Selenium is required but not available in this environment. Please install Selenium and chromedriver.")

    def _init_selenium_settings(self):
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

    # ---------- Cookie helpers ----------
    def _load_cookies_into_requests(self) -> requests.Session:
        """
        Load cookies saved from Selenium into a requests.Session for non-Selenium requests.
        """
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

    def _save_cookies_from_selenium(self) -> None:
        """
        Save Selenium cookies to disk for later requests usage.
        """
        if not self.driver:
            return
        try:
            cookies = self.driver.get_cookies()
            with open(self.cookie_file, 'wb') as f:
                pickle.dump(cookies, f)
            LOG.debug("Saved selenium cookies")
        except Exception:
            LOG.exception("Failed to save selenium cookies")

    def _cookies_indicate_logged_in(self, cookies_list: List[Dict[str, Any]]) -> bool:
        for c in cookies_list:
            n = c.get('name')
            if not n:
                continue
            if any(k in n.lower() for k in LIKELY_SESSION_COOKIE_NAMES):
                return True
        return False

    def login_selenium_with_cookies(self) -> bool:
        """
        Load cookies from disk (if any) into Selenium and refresh to try to resume session.
        Returns True if cookies indicate a logged-in state after load.
        """
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

    # ---------- Utility parsing helpers ----------
    def _parse_series_from_title(self, title: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
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

        # parentheses at end e.g. "Book Title (Series #1)"
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

        # alternate format "Title - Series #1"
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

    def _normalize_author_first(self, author_text: Optional[str]) -> Optional[str]:
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

    def _extract_text(self, el):
        """Safe .get_text(strip=True) wrapper that returns None for falsy elements"""
        if not el:
            return None
        t = el.get_text(" ", strip=True)
        return t if t != "" else None

    def _map_row_by_headers(self, headers: List[str], row) -> Dict[str, Any]:
        """
        Try to map cells to headers (fallback mechanism when field classes not present).
        This returns a dict mapping header->value, but we will post-process later to normalize.
        """
        cells = row.find_all(['td', 'th'])
        data: Dict[str, Any] = {}
        for idx, h in enumerate(headers):
            key = h or f'col{idx}'
            val = None
            if idx < len(cells):
                cell = cells[idx]
                # prefer anchors with text
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
                    img = cell.find('img')
                    if key == 'cover' and img and img.get('src'):
                        val = img.get('src')
                    else:
                        val = cell.get_text(" ", strip=True)
                # numeric conversions
                if val:
                    if key in ('position', 'num_pages', 'num_ratings', 'comments', 'votes', 'read_count'):
                        m = re.search(r'(\d+)', str(val).replace(',', ''))
                        if m:
                            try:
                                val = int(m.group(1))
                            except Exception:
                                pass
                    if key in ('avg_rating',):
                        m = re.search(r'([0-9]+(\.[0-9]+)?)', str(val))
                        if m:
                            try:
                                val = float(m.group(1))
                            except Exception:
                                pass
            data[key] = val
        return data

    def _extract_field_via_cls(self, row, field_name: str):
        """
        Preferred: use explicit 'field <name>' cells when present e.g. <td class="field title">.
        Returns the text content or structured info depending on field_name.
        """
        try:
            sel = row.select_one(f".field.{field_name}") or row.select_one(f"td.{field_name}") or row.select_one(f".{field_name}")
            if not sel:
                return None
            # special handling
            if field_name == 'cover':
                img = sel.find('img')
                if img and img.get('src'):
                    return img.get('src')
                # sometimes anchor wraps the img
                a = sel.find('a')
                if a:
                    img = a.find('img')
                    if img and img.get('src'):
                        return img.get('src')
                return self._extract_text(sel)
            if field_name == 'title':
                a = sel.find('a', href=True)
                if a:
                    # include span text
                    return a.get_text(" ", strip=True)
                return self._extract_text(sel)
            if field_name == 'author':
                a = sel.find('a', href=True)
                if a:
                    return a.get_text(" ", strip=True)
                return self._extract_text(sel)
            if field_name in ('isbn', 'isbn13', 'asin'):
                # just return cell text if present - we'll normalise later
                return self._extract_text(sel)
            if field_name == 'rating':
                # check for <div class="stars" data-rating="4">
                stars = sel.find('div', class_='stars')
                if stars and stars.get('data-rating'):
                    try:
                        dr = int(stars.get('data-rating'))
                        return f"{dr} of 5 stars"
                    except Exception:
                        pass
                # fallback to text
                return self._extract_text(sel)
            if field_name in ('comments', 'votes', 'num_ratings'):
                a = sel.find('a')
                if a:
                    return self._extract_text(a)
                return self._extract_text(sel)
            if field_name in ('date_added',):
                # prefer span[title] for exact date
                sp = sel.find('span', title=True)
                if sp and sp.get('title'):
                    return sp.get('title')
                return self._extract_text(sel)
            # default
            return self._extract_text(sel)
        except Exception:
            LOG.debug("Failed to extract field %s via class", field_name, exc_info=True)
            return None

    def _normalize_isbn_like(self, raw: Optional[str]) -> Optional[str]:
        if not raw:
            return None
        # remove prefixes like "isbn", "isbn13", "asin"
        r = raw.strip()
        r = re.sub(r'^(isbn13|isbn|asin)[:\s]*', '', r, flags=re.I)
        r = r.strip()
        return r if r else None

    # ---------- Core shelf fetcher (ALL + filter) ----------
    def get_goodreads_books_from_shelf(self, shelf_name: str = 'all', fetch_details: bool = False, max_pages: int = 200) -> List[Dict[str, Any]]:
        """
        Fetch books using the 'ALL' shelf view with pagination using per_page configured.
        Uses Selenium for correct settings and ensures all columns / shelves anchors are visible.
        If shelf_name != 'all', we filter results in Python.
        """
        LOG.info("Fetching books from Goodreads shelf='%s' user=%s per_page=%d", shelf_name, self.user_id, self.per_page)

        if not self.user_id:
            raise ValueError("goodreads_user_id not set in config")

        collected: List[Dict[str, Any]] = []
        page_index = 1
        total_expected: Optional[int] = None

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

                # make sure the table shows all columns and per_page setting
                try:
                    self.ensure_all_columns_visible()
                except Exception:
                    LOG.debug("ensure_all_columns_visible encountered an issue")

                # try reading a total count
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

                # wait for table rows to appear (best-effort)
                try:
                    WebDriverWait(drv, 6).until(
                        lambda d: len(d.find_elements(By.CSS_SELECTOR, "tbody#booksBody tr, tr.bookalike, table#books tr")) > 0
                    )
                except Exception:
                    LOG.debug("Timed out waiting for shelf table; parsing whatever is present")

                html = drv.page_source
                page_books, has_next = self._parse_shelf_html_and_save(html, 'all', fetch_details)
                collected.extend(page_books)
                LOG.info("Page %d: collected %d books (cumulative %d)", page_index, len(page_books), len(collected))

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

        # Filter if a specific shelf requested
        if shelf_name and shelf_name.lower() != 'all':
            filtered = [b for b in collected if b.get('shelves') and shelf_name.lower() in b.get('shelves').lower()]
            LOG.info("Filtered %d books for shelf='%s' from %d total", len(filtered), shelf_name, len(collected))
            return filtered

        LOG.info("Total books collected from ALL shelves: %d (expected=%s)", len(collected), total_expected)
        return collected

    # ---------- Parse & save logic ----------
    def _parse_shelf_html_and_save(self, html: str, shelf_name: str, fetch_details: bool = False) -> Tuple[List[Dict[str, Any]], bool]:
        """
        Parse a Goodreads shelf page (table view) and store rows into DB.
        Returns (books_list, has_next_page)
        """
        soup = BeautifulSoup(html, 'html.parser')
        # detect pagination next link
        has_next = False
        try:
            next_link = soup.select_one('a.next_page')
            if next_link and ('disabled' not in (next_link.get('class') or [])) and next_link.get('href'):
                has_next = True
            if not has_next:
                rel_next = soup.find('a', rel='next')
                if rel_next and rel_next.get('href'):
                    has_next = True
        except Exception:
            has_next = False

        # detect headers
        headers: List[str] = []
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
                elif raw.strip() in ('rating', 'my rating'):
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
            headers = ['position', 'cover', 'title', 'author', 'isbn', 'avg_rating', 'num_ratings', 'date_pub', 'rating', 'shelves', 'review', 'notes', 'comments', 'votes', 'date_read', 'date_added', 'format', 'actions']

        books: List[Dict[str, Any]] = []
        rows = soup.select('tbody#booksBody tr') or soup.select('tr.bookalike') or []
        for row in rows:
            try:
                mapped = self._map_row_by_headers(headers, row)

                # Prefer extracting some fields with reliable selectors
                # Extract goodreads id
                gid: Optional[str] = None
                if mapped.get('book_url'):
                    m = re.search(r'/book/show/(\d+)', str(mapped.get('book_url')) or "")
                    if m:
                        gid = m.group(1)
                    else:
                        m2 = re.search(r'(\d{6,})', str(mapped.get('book_url')) or "")
                        if m2:
                            gid = m2.group(1)
                if not gid:
                    if row.has_attr('id'):
                        m3 = re.search(r'(\d{6,})', row['id'])
                        if m3:
                            gid = m3.group(1)
                if not gid:
                    for attr in ('data-resource-id', 'data-book-id', 'data-review-id'):
                        if row.has_attr(attr):
                            gid = row[attr]
                            break
                if not gid:
                    LOG.debug("Skipping row without goodreads id (title=%s)", mapped.get('title'))
                    continue

                # Field-specific extraction (prefer these over mapped)
                title_raw = self._extract_field_via_cls(row, 'title') or mapped.get('title') or None
                title_clean, series_name, series_number, series_id = self._parse_series_from_title(title_raw or "")
                author = self._extract_field_via_cls(row, 'author') or mapped.get('author') or None
                author_first = self._normalize_author_first(author)

                cover_url = self._extract_field_via_cls(row, 'cover') or mapped.get('cover') or None
                cover_local = None
                try:
                    cover_local = self._download_cover(cover_url, gid) if cover_url else None
                except Exception:
                    cover_local = None

                # shelves extraction
                shelves_list: List[str] = []
                try:
                    shelves_cell = None
                    if 'shelves' in headers:
                        try:
                            idx = headers.index('shelves')
                            cells = row.find_all(['td', 'th'])
                            if idx < len(cells):
                                shelves_cell = cells[idx]
                        except Exception:
                            shelves_cell = None
                    if not shelves_cell:
                        shelves_cell = row.select_one('.field.shelves, td.field.shelves, td.shelves, .shelfList, .shelfLink') or row

                    if shelves_cell:
                        for a in shelves_cell.find_all('a', class_='shelfLink'):
                            text = (a.get_text(" ", strip=True) or "").strip()
                            if text:
                                shelves_list.append(text)
                        if not shelves_list:
                            raw_shelves = self._extract_text(shelves_cell)
                            if raw_shelves:
                                parts = [s.strip() for s in re.split(r'[,/\\;]|(?:\s+and\s+)', raw_shelves) if s.strip()]
                                shelves_list.extend(parts)
                except Exception:
                    LOG.debug("Failed to parse shelves from row")

                if not shelves_list:
                    shelves_list = [shelf_name]

                # normalize shelf names
                norm_shelves = []
                for s in shelves_list:
                    s2 = re.sub(r'\s*\(.*?\)\s*', '', s).strip()
                    if s2:
                        norm_shelves.append(s2)
                if not norm_shelves:
                    norm_shelves = [shelf_name]

                shelves_str = ", ".join(norm_shelves)

                # extract many other fields via selectors and fallback to mapped
                isbn_raw = self._extract_field_via_cls(row, 'isbn') or mapped.get('isbn')
                isbn13_raw = self._extract_field_via_cls(row, 'isbn13') or mapped.get('isbn13')
                asin_raw = self._extract_field_via_cls(row, 'asin') or mapped.get('asin')
                num_pages = self._extract_field_via_cls(row, 'num_pages') or mapped.get('num_pages')
                try:
                    # normalize "300 pp" or "300"
                    if isinstance(num_pages, str):
                        m = re.search(r'(\d+)', num_pages.replace(',', ''))
                        if m:
                            num_pages = int(m.group(1))
                except Exception:
                    pass

                avg_rating = self._extract_field_via_cls(row, 'avg_rating') or mapped.get('avg_rating')
                num_ratings = self._extract_field_via_cls(row, 'num_ratings') or mapped.get('num_ratings')
                try:
                    if isinstance(num_ratings, str):
                        m = re.search(r'(\d+)', num_ratings.replace(',', ''))
                        if m:
                            num_ratings = int(m.group(1))
                except Exception:
                    pass

                date_pub = self._extract_field_via_cls(row, 'date_pub') or mapped.get('date_pub')
                date_pub_edition = self._extract_field_via_cls(row, 'date_pub_edition') or mapped.get('date_pub_edition')
                rating = self._extract_field_via_cls(row, 'rating') or mapped.get('rating')
                review_text = self._extract_field_via_cls(row, 'review') or mapped.get('review')
                notes = self._extract_field_via_cls(row, 'notes') or mapped.get('notes')
                comments = self._extract_field_via_cls(row, 'comments') or mapped.get('comments')
                votes = self._extract_field_via_cls(row, 'votes') or mapped.get('votes')
                read_count = self._extract_field_via_cls(row, 'read_count') or mapped.get('read_count')
                date_read = self._extract_field_via_cls(row, 'date_read') or mapped.get('date_read')
                date_started = self._extract_field_via_cls(row, 'date_started') or mapped.get('date_started')
                date_added = self._extract_field_via_cls(row, 'date_added') or mapped.get('date_added')
                owned = self._extract_field_via_cls(row, 'owned') or mapped.get('owned')
                fmt = self._extract_field_via_cls(row, 'format') or mapped.get('format')

                # normalization for isbn/asin
                isbn_norm = self._normalize_isbn_like(isbn_raw) if isbn_raw else None
                isbn13_norm = self._normalize_isbn_like(isbn13_raw) if isbn13_raw else None
                asin_norm = self._normalize_isbn_like(asin_raw) if asin_raw else None

                # json_details: keep the raw mapped map (best-effort)
                json_details = None
                try:
                    json_details = json.dumps(mapped, ensure_ascii=False)
                except Exception:
                    try:
                        json_details = json.dumps(str(mapped))
                    except Exception:
                        json_details = None

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
                    'cover_local_path': cover_local,
                    'book_url': mapped.get('book_url'),
                    'isbn': isbn_norm,
                    'isbn13': isbn13_norm,
                    'asin': asin_norm,
                    'avg_rating': avg_rating,
                    'num_ratings': num_ratings,
                    'date_pub': date_pub,
                    'date_pub_edition': date_pub_edition,
                    'num_pages': num_pages,
                    'rating': rating,
                    'shelves': shelves_str,
                    'review': review_text,
                    'notes': notes,
                    'comments': comments,
                    'votes': votes,
                    'date_read': date_read,
                    'date_started': date_started,
                    'date_added': date_added,
                    'date_purchased': mapped.get('date_purchased'),
                    'purchase_location': mapped.get('purchase_location'),
                    'owned': owned,
                    'condition': mapped.get('condition'),
                    'format': fmt,
                    'recommender': mapped.get('recommender'),
                    'read_count': read_count,
                    'genres': None,
                    'json_details': json_details,
                    'fetched_at': datetime.utcnow().isoformat()
                }

                # Save to DB (shelves list passed explicitly)
                try:
                    self.db.save_book(book, shelves=norm_shelves)
                except Exception:
                    LOG.exception("Failed to save book to DB: %s", gid)

                # Add history for each parsed row
                try:
                    self.db.add_history(action='fetch_shelf_row', book_id=gid, title=title_clean, status='fetched', meta={'shelves': norm_shelves})
                except Exception:
                    pass

                books.append(book)
            except Exception:
                LOG.exception("Failed to parse/save row")
                continue

        return books, has_next

    # ---------- Utilities for review editing and shelf updating ----------
    def get_review_edit_url(self, book_id: str, review_id: Optional[str] = None) -> str:
        """
        Returns the Goodreads review edit URL. review_id often == book_id in the web UI.
        """
        rid = review_id or book_id
        return f"{self.base_url}/review/edit/{rid}?report_event=true"

    def fetch_review_edit_form(self, book_id: str) -> Dict[str, Any]:
        """
        Fetch /review/edit/{book_id} page using cookies and return parsed authenticity_token and
        a few defaults present in the form. Does NOT change anything on Goodreads.
        """
        sess = self._load_cookies_into_requests()
        url = self.get_review_edit_url(book_id)
        try:
            resp = sess.get(url, timeout=20)
            if resp.status_code != 200:
                LOG.warning("Failed to fetch review edit page %s (status=%s)", url, resp.status_code)
                return {'ok': False, 'status_code': resp.status_code}
            soup = BeautifulSoup(resp.text, 'html.parser')
            form = soup.find('form', {'id': re.compile(r'form_review_.*')})
            token = None
            if form:
                inp = form.find('input', {'name': 'authenticity_token'})
                if inp and inp.get('value'):
                    token = inp['value']
            # get current rating from stars div (if present)
            stars = soup.find('div', class_='stars')
            current_rating = None
            if stars and stars.get('data-rating'):
                try:
                    current_rating = int(stars.get('data-rating'))
                except Exception:
                    current_rating = None

            # current shelves shown in the review_shelfList* container
            shelf_list_el = soup.select_one('[id^=review_shelfList]')
            shelves = []
            if shelf_list_el:
                for a in shelf_list_el.find_all('a', class_='shelfLink'):
                    txt = a.get_text(" ", strip=True)
                    if txt:
                        shelves.append(txt)
            return {
                'ok': True,
                'authenticity_token': token,
                'current_rating': current_rating,
                'shelves': shelves,
                'html': resp.text
            }
        except Exception:
            LOG.exception("Failed to fetch review edit form for %s", book_id)
            return {'ok': False, 'error': 'exception'}

    def submit_review_edit(self, book_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Minimal attempt to submit /review/update/{book_id} via requests using cookies.
        payload can contain common params, for example:
            {
                "review[review]": "My review text",
                "review[spoiler_flag]": 0,
                "add_update": "1",
                "authenticity_token": "<token from fetch_review_edit_form>"
            }
        Note: Goodreads forms have many dynamic fields; this function attempts basic submits.
        """
        sess = self._load_cookies_into_requests()
        # Ensure token present
        token = payload.get('authenticity_token')
        if not token:
            # try to fetch the form first
            info = self.fetch_review_edit_form(book_id)
            if info.get('ok'):
                token = info.get('authenticity_token')
        if not token:
            return {'ok': False, 'error': 'missing_auth_token'}

        url = f"{self.base_url}/review/update/{book_id}"
        data = dict(payload)
        data['authenticity_token'] = token
        try:
            resp = sess.post(url, data=data, timeout=20)
            return {'ok': resp.status_code in (200, 302), 'status_code': resp.status_code, 'text': resp.text}
        except Exception:
            LOG.exception("Failed to submit review edit for %s", book_id)
            return {'ok': False, 'error': 'exception'}

    def update_shelves_via_requests(self, book_id: str, add_shelves: Optional[List[str]] = None, remove_shelves: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Update shelves by calling Goodreads AJAX endpoint '/shelf/add_to_shelf' using loaded cookies.
        For each shelf in add_shelves -> call with name=<shelf> (add)
        For each shelf in remove_shelves -> call with name=<shelf>&a=remove (remove)
        Returns summary results.
        """
        add_shelves = add_shelves or []
        remove_shelves = remove_shelves or []
        sess = self._load_cookies_into_requests()

        # Need authenticity_token - try to fetch from a book page
        token = None
        try:
            book_url = f"{self.base_url}/book/show/{book_id}"
            r = sess.get(book_url, timeout=15)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                token_el = soup.find('meta', {'name': 'csrf-token'}) or soup.find('input', {'name': 'authenticity_token'})
                if token_el:
                    token = token_el.get('content') or token_el.get('value')
            if not token:
                # fallback: attempt review edit form
                info = self.fetch_review_edit_form(book_id)
                token = info.get('authenticity_token') if info.get('ok') else token
        except Exception:
            LOG.debug("Failed to fetch auth token from book page", exc_info=True)

        if not token:
            return {'ok': False, 'error': 'missing_auth_token'}

        results = {'added': [], 'removed': [], 'failed': []}
        endpoint = f"{self.base_url}/shelf/add_to_shelf"
        headers = {'Referer': f"{self.base_url}/book/show/{book_id}"}
        for shelf in add_shelves:
            try:
                payload = {
                    'book_id': book_id,
                    'name': shelf,
                    'a': '',  # add
                    'authenticity_token': token
                }
                r = sess.post(endpoint, data=payload, headers=headers, timeout=15)
                if r.status_code in (200, 302):
                    results['added'].append(shelf)
                else:
                    results['failed'].append({'shelf': shelf, 'status': r.status_code})
            except Exception:
                LOG.exception("Failed to add shelf %s for book %s", shelf, book_id)
                results['failed'].append({'shelf': shelf, 'error': 'exception'})

        for shelf in remove_shelves:
            try:
                payload = {
                    'book_id': book_id,
                    'name': shelf,
                    'a': 'remove',
                    'authenticity_token': token
                }
                r = sess.post(endpoint, data=payload, headers=headers, timeout=15)
                if r.status_code in (200, 302):
                    results['removed'].append(shelf)
                else:
                    results['failed'].append({'shelf': shelf, 'status': r.status_code})
            except Exception:
                LOG.exception("Failed to remove shelf %s for book %s", shelf, book_id)
                results['failed'].append({'shelf': shelf, 'error': 'exception'})

        return {'ok': True, 'result': results}

    def update_shelves_via_selenium(self, book_id: str, desired_shelves: List[str], wait_secs: float = 0.2) -> bool:
        """
        Use the Goodreads shelf chooser via Selenium to set the chosen shelves.
        desired_shelves should be final chosen shelves you want for the book (list of tokens).
        This function opens the book page and toggles the chooser checkboxes to reach the desired state.
        """
        drv = self._init_driver()
        if not drv:
            LOG.error("Selenium not available for update_shelves_via_selenium")
            return False

        book_url = f"{self.base_url}/book/show/{book_id}"
        try:
            drv.get(book_url)
            time.sleep(1.0)
            # Click the [edit] shelf chooser link (there are multiple variants; prefer .shelfChooserLink)
            try:
                edit_link = WebDriverWait(drv, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, ".shelfChooserLink, .shelfChooserLink.smallText"))
                )
                drv.execute_script("arguments[0].click();", edit_link)
            except Exception:
                # maybe the chooser is visible or different selector - try another approach
                try:
                    # find any anchor that summons shelfChooser via onclick containing 'shelfChooser.summon'
                    anchors = drv.find_elements(By.XPATH, "//a[contains(@onclick,'shelfChooser.summon') or contains(@onclick,'window.shelfChooser.summon')]")
                    if anchors:
                        drv.execute_script("arguments[0].click();", anchors[0])
                except Exception:
                    LOG.debug("Could not open shelf chooser for book %s", book_id)

            # Wait for the chooser wrapper to appear
            try:
                chooser = WebDriverWait(drv, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".shelfChooserWrapper.open, .shelfChooserWrapper"))
                )
            except Exception:
                chooser = None

            if not chooser:
                LOG.debug("Shelf chooser not found for book %s", book_id)
                return False

            # Find shelf <li> elements
            try:
                lis = chooser.find_elements(By.CSS_SELECTOR, "li")
            except Exception:
                lis = []

            # Build a map alt->li element and whether currently chosen
            alt_map = {}
            for li in lis:
                try:
                    alt = li.get_attribute("alt") or (li.text or "").strip()
                    cls = li.get_attribute("class") or ""
                    chosen = 'chosen' in cls or 'exclusive_chosen' in cls or 'visible chosen' in cls
                    alt_map[alt] = {'el': li, 'chosen': chosen}
                except Exception:
                    continue

            # For each available shelf in chooser toggle to desired state
            for alt, info in alt_map.items():
                want = alt in desired_shelves
                currently = info.get('chosen', False)
                if want != currently:
                    try:
                        drv.execute_script("arguments[0].click();", info['el'])
                        time.sleep(wait_secs)
                    except Exception:
                        # try to click child <span>
                        try:
                            child = info['el'].find_element(By.TAG_NAME, "span")
                            drv.execute_script("arguments[0].click();", child)
                            time.sleep(wait_secs)
                        except Exception:
                            LOG.debug("Could not toggle shelf %s for book %s", alt, book_id)

            # Close chooser by clicking "close" link if present
            try:
                close_btn = chooser.find_element(By.CSS_SELECTOR, "a.right, a.close, a.greyText")
                drv.execute_script("arguments[0].click();", close_btn)
            except Exception:
                # attempt to click outside or press escape via JS
                try:
                    drv.execute_script("document.body.click();")
                except Exception:
                    pass

            # Save cookies after change
            try:
                self._save_cookies_from_selenium()
            except Exception:
                LOG.debug("Unable to save cookies after shelf update")

            LOG.info("update_shelves_via_selenium completed for %s", book_id)
            return True
        except Exception:
            LOG.exception("Failed to update shelves via selenium for %s", book_id)
            return False

    # ---------- Misc helpers ----------
    def vacuum(self) -> None:
        try:
            self.db.vacuum()
        except Exception:
            LOG.exception("VACUUM failed")

    def row_count(self) -> int:
        try:
            return self.db.row_count()
        except Exception:
            LOG.exception("Failed to count rows")
            return 0

    def close(self) -> None:
        try:
            self.db.close()
            self._close_driver()
        except Exception:
            pass

    def edit_review_via_selenium(
        self,
        book_id: str,
        rating: Optional[int] = None,
        review_text: Optional[str] = None,
        shelves: Optional[List[str]] = None,
        wait_secs: float = 0.3
    ) -> bool:
        """
        Open Goodreads book review edit form using Selenium and update fields.
        Arguments:
          book_id      : Goodreads book id (string or int).
          rating       : Integer 1–5 for star rating (None = leave unchanged).
          review_text  : Text string for review body (None = leave unchanged).
          shelves      : List of shelf tokens to set (None = leave unchanged).
          wait_secs    : Delay between Selenium actions (default 0.3s).

        Returns True if update likely succeeded, False otherwise.
        """
        drv = self._init_driver()
        if not drv:
            LOG.error("Selenium not available for edit_review_via_selenium")
            return False

        review_url = f"{self.base_url}/review/edit/{book_id}?report_event=true"
        try:
            drv.get(review_url)
            time.sleep(1.0)

            # Wait for form to load
            try:
                form = WebDriverWait(drv, 6).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "form[id^=form_review_]"))
                )
            except Exception:
                LOG.error("Review edit form not found for book %s", book_id)
                return False

            # Update rating if requested
            if rating and 1 <= rating <= 5:
                try:
                    stars = form.find_elements(By.CSS_SELECTOR, "div.stars a.star")
                    if stars and len(stars) >= rating:
                        drv.execute_script("arguments[0].click();", stars[rating - 1])
                        time.sleep(wait_secs)
                except Exception:
                    LOG.debug("Could not set rating for book %s", book_id, exc_info=True)

            # Update review text if requested
            if review_text is not None:
                try:
                    textarea = form.find_element(By.CSS_SELECTOR, "textarea[name='review[review]']")
                    textarea.clear()
                    textarea.send_keys(review_text)
                    time.sleep(wait_secs)
                except Exception:
                    LOG.debug("Could not set review text for book %s", book_id, exc_info=True)

            # Update shelves if requested
            if shelves is not None:
                try:
                    # Click the shelf chooser link
                    chooser_link = form.find_element(By.CSS_SELECTOR, ".shelfChooserLink")
                    drv.execute_script("arguments[0].click();", chooser_link)
                    time.sleep(0.6)
                    chooser = WebDriverWait(drv, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, ".shelfChooserWrapper.open"))
                    )
                    lis = chooser.find_elements(By.CSS_SELECTOR, "li[alt]")
                    for li in lis:
                        alt = li.get_attribute("alt")
                        chosen = 'chosen' in (li.get_attribute("class") or "")
                        want = alt in shelves
                        if want != chosen:
                            drv.execute_script("arguments[0].click();", li)
                            time.sleep(wait_secs)
                    # Close chooser
                    try:
                        close_btn = chooser.find_element(By.CSS_SELECTOR, "a.right, a.close, a.greyText")
                        drv.execute_script("arguments[0].click();", close_btn)
                        time.sleep(wait_secs)
                    except Exception:
                        pass
                except Exception:
                    LOG.debug("Could not update shelves for book %s", book_id, exc_info=True)

            # Submit form
            try:
                save_btn = form.find_element(By.CSS_SELECTOR, "input[type=submit], .gr-button")
                drv.execute_script("arguments[0].click();", save_btn)
                time.sleep(2.0)
            except Exception:
                LOG.error("Could not click save/submit for book %s", book_id)
                return False

            # Save cookies back
            try:
                self._save_cookies_from_selenium()
            except Exception:
                LOG.debug("Unable to save cookies after review edit")

            LOG.info("edit_review_via_selenium completed for %s", book_id)
            return True
        except Exception:
            LOG.exception("Failed edit_review_via_selenium for book %s", book_id)
            return False



    # You can add more high-level helpers that combine fetch->process->update flows as needed.
