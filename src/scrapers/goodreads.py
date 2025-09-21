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
from urllib.parse import urljoin, urlencode, urlparse

from bs4 import BeautifulSoup

# Selenium imports (optionally used)
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, WebDriverException
    SELENIUM_AVAILABLE = True
except Exception:
    SELENIUM_AVAILABLE = False

from src.utils.database import Database

LOG = logging.getLogger(__name__)

LIKELY_SESSION_COOKIE_NAMES = ['session-id', 'sess', 's', 'auth_token', 'gr_user', 'cgcsess', 'session']


class GoodreadsScraper:
    def __init__(self, config: dict):
        """
        config keys used:
          - goodreads_user_id
          - cache_dir
          - database_path
          - per_page (optional; default 100)
          - download_backend_url (optional)
        """
        self.config = config or {}
        self.user_id = str(self.config.get("goodreads_user_id") or "")
        self.base_url = "https://www.goodreads.com"
        cache_dir = Path(self.config.get("cache_dir", "/app/data/cache"))
        self.cache_dir = cache_dir
        self.cookie_file = cache_dir / "logins" / "goodreads" / "cookies.pkl"
        (self.cookie_file.parent).mkdir(parents=True, exist_ok=True)
        self.cover_cache = cache_dir / "covers"
        self.cover_cache.mkdir(parents=True, exist_ok=True)
        self.search_requests_dir = cache_dir / "search_requests"
        self.search_requests_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.config.get("database_path"))
        self.per_page = int(self.config.get("goodreads_per_page", 100))
        self.max_pages = int(self.config.get("goodreads_max_pages", 50))
        self.driver = None
        self._selenium_available = SELENIUM_AVAILABLE
        # selenium options
        self.headless = os.getenv("HEADLESS", "1") == "1"
        self.chromium_bin = os.getenv("CHROMIUM_BIN", "/usr/bin/chromium")
        self.chromedriver_path = os.getenv("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")
        self.download_backend_url = self.config.get("download_backend_url", os.getenv("DOWNLOAD_BACKEND_URL", "http://localhost:8080"))
        self.init_driver_if_possible()

    def init_driver_if_possible(self):
        if not self._selenium_available:
            return None
        try:
            options = Options()
            if self.headless:
                # modern selenium Chrome headless mode
                options.add_argument("--headless=new")
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
        except Exception:
            LOG.exception("Failed to init Selenium driver")
            self._selenium_available = False
            self.driver = None
            return None

    def close_driver(self):
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
            with open(self.cookie_file, "rb") as f:
                cookies = pickle.load(f)
            for c in cookies:
                name = c.get("name")
                value = c.get("value")
                domain = c.get("domain", ".goodreads.com")
                path = c.get("path", "/")
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
            with open(self.cookie_file, "wb") as f:
                pickle.dump(cookies, f)
            LOG.debug("Saved selenium cookies")
        except Exception:
            LOG.exception("Failed to save selenium cookies")

    def _cookies_indicate_logged_in(self, cookies_list):
        for c in cookies_list:
            n = c.get("name")
            if not n:
                continue
            if any(k in n.lower() for k in LIKELY_SESSION_COOKIE_NAMES):
                return True
        return False

    def login_selenium_with_cookies(self):
        drv = self.driver or self.init_driver_if_possible()
        if not drv:
            return False
        try:
            drv.get(self.base_url)
            if self.cookie_file.exists():
                with open(self.cookie_file, "rb") as f:
                    cookies = pickle.load(f)
                for c in cookies:
                    try:
                        c.pop("sameSite", None)
                        if 'expiry' in c and isinstance(c['expiry'], float):
                            c['expiry'] = int(c['expiry'])
                        if 'domain' in c and c['domain'].startswith('.'):
                            c['domain'] = c['domain'].lstrip('.')
                        drv.add_cookie(c)
                    except Exception:
                        continue
                time.sleep(0.4)
                drv.refresh()
                time.sleep(0.6)
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
        """If the shelf settings control is present, click it, enable all checkboxes, set per_page."""
        if not self.driver:
            return
        try:
            # click settings link if present
            try:
                link = self.driver.find_element(By.ID, "shelfSettingsLink")
                try:
                    self.driver.execute_script("arguments[0].click();", link)
                    time.sleep(0.4)
                except Exception:
                    try:
                        link.click()
                        time.sleep(0.4)
                    except:
                        pass
            except Exception:
                pass

            try:
                settings = self.driver.find_element(By.ID, "shelfSettings")
                checkboxes = settings.find_elements(By.CSS_SELECTOR, "input[type=checkbox]")
                for cb in checkboxes:
                    try:
                        if not cb.is_selected():
                            self.driver.execute_script("arguments[0].click();", cb)
                    except Exception:
                        continue
                # set per_page control if present
                try:
                    sel = settings.find_element(By.ID, "user_shelf_per_page")
                    self.driver.execute_script("arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('change'))", sel, str(self.per_page))
                except Exception:
                    pass
                # click Save button
                try:
                    save_btn = settings.find_element(By.ID, "save_curr_sett_submit")
                    self.driver.execute_script("arguments[0].click()", save_btn)
                    time.sleep(0.6)
                except Exception:
                    pass
            except Exception:
                pass
        except Exception:
            LOG.exception("Failed to ensure all columns visible")

    def _download_cover(self, url, goodreads_id):
        if not url:
            return None
        parsed = urlparse(url)
        ext = Path(parsed.path).suffix or ".jpg"
        fname = f"{goodreads_id}{ext}"
        local = self.cover_cache / fname
        if local.exists():
            return fname
        try:
            resp = requests.get(url, timeout=20, stream=True)
            if resp.status_code == 200:
                with open(local, "wb") as f:
                    for chunk in resp.iter_content(1024 * 8):
                        f.write(chunk)
                return fname
        except Exception:
            LOG.exception("Failed to download cover: %s", url)
        return None

    def _parse_series_from_title(self, title):
        if not title:
            return title, None, None, None
        t = str(title).strip()
        series_name = None
        series_number = None
        series_id = None

        # parentheses at end
        m = re.search(r'\(([^()]+)\)\s*$', t)
        if m:
            inside = m.group(1)
            mnum = re.search(r'#\s*([\d\.]+)', inside)
            if mnum:
                series_number = mnum.group(1)
            cleaned = re.sub(r',?\s*#\s*[\d\.]+', '', inside).strip()
            series_name = cleaned if cleaned else None
            title_clean = re.sub(r'\s*\([^()]*\)\s*$', '', t).strip()
            return title_clean, series_name, series_number, series_id

        # other patterns
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
        if not author_text:
            return None
        a = author_text.strip()
        if ',' in a:
            parts = [p.strip() for p in a.split(',') if p.strip()]
            if len(parts) >= 2:
                return " ".join(parts[::-1])
        return a

    def _map_row_by_headers(self, headers, row):
        cells = row.find_all(['td', 'th'])
        data = {}
        for idx, h in enumerate(headers):
            key = h or f'col{idx}'
            val = None
            if idx < len(cells):
                cell = cells[idx]
                # prefer anchor text
                a = cell.find('a')
                if a and a.get_text(strip=True):
                    if key == 'title':
                        val = a.get_text(" ", strip=True)
                        href = a.get('href')
                        data['book_url'] = urljoin(self.base_url, href) if href else None
                    elif key == 'author':
                        val = a.get_text(" ", strip=True)
                    else:
                        # cover may be img inside
                        img = cell.find('img')
                        if img and img.get('src'):
                            val = img.get('src')
                        else:
                            val = a.get_text(" ", strip=True)
                else:
                    # fallback: images / text
                    img = cell.find('img')
                    if img and img.get('src'):
                        val = img.get('src')
                    else:
                        val = cell.get_text(" ", strip=True)

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

    def _parse_shelf_html_and_save(self, html, shelf_name, fetch_details=False):
        soup = BeautifulSoup(html, "html.parser")
        # next detection
        has_next = False
        try:
            next_link = soup.select_one('a.next_page') or soup.find('a', string=re.compile(r'next', re.I))
            if next_link and ('disabled' not in (next_link.get('class') or [])):
                has_next = True
        except Exception:
            has_next = False

        headers = []
        header_row = soup.select_one('table#books thead tr') or soup.select_one('thead tr')
        if header_row:
            for th in header_row.find_all('th'):
                alt = th.get('alt') or (th.get('class') and " ".join(th.get('class'))) or th.get_text(" ", strip=True)
                raw = alt.strip().lower()
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
                elif 'num pages' in raw or 'num_pages' in raw:
                    headers.append('num_pages')
                elif 'rating' in raw and 'my rating' in raw:
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
            # fallback default order
            headers = ['position','cover','title','author','isbn','isbn13','asin','num_pages','avg_rating','num_ratings','date_pub','date_pub_edition','rating','shelves','review','notes','comments','votes','read_count','date_started','date_read','date_added','owned','format','actions']

        rows = soup.select('tbody#booksBody tr') or soup.select('tr.bookalike') or []
        books = []
        for row in rows:
            try:
                mapped = self._map_row_by_headers(headers, row)
                gid = None
                if mapped.get('book_url'):
                    m = re.search(r'/book/show/(\d+)', mapped.get('book_url') or "")
                    if m:
                        gid = m.group(1)
                if not gid:
                    if row.has_attr('id'):
                        m3 = re.search(r'(\d{5,})', row['id'])
                        if m3:
                            gid = m3.group(1)
                    if not gid:
                        for attr in ('data-resource-id','data-book-id','data-review-id'):
                            if row.has_attr(attr):
                                gid = row[attr]
                                break
                if not gid:
                    LOG.debug("Skipping row without goodreads id, title=%s", mapped.get('title'))
                    continue

                title_raw = mapped.get('title') or None
                title_clean, series_name, series_number, series_id = self._parse_series_from_title(title_raw)
                author = mapped.get('author') or None
                author_first = self._normalize_author_first(author)

                cover_url = mapped.get('cover') or None
                cover_local = None
                try:
                    cover_local = self._download_cover(cover_url, gid) if cover_url else None
                except Exception:
                    cover_local = None

                shelves_val = mapped.get('shelves')
                if shelves_val:
                    # join anchors (if raw text) or use string
                    shelves = ", ".join([s.strip() for s in re.split(r'[,/]|\\|;', shelves_val) if s.strip()])
                else:
                    shelves = None

                # If series id missing, attempt to fetch book page to find series link
                if not series_id:
                    book_url = mapped.get('book_url')
                    if book_url:
                        try:
                            r = requests.get(book_url, timeout=10)
                            if r.status_code == 200:
                                bs = BeautifulSoup(r.text, 'html.parser')
                                # find series link
                                ser = bs.select_one('a[href*="/series/"], span.darkGreyText')
                                if ser:
                                    # look for href like /series/331134-the-cassie...
                                    a = ser if ser.name == 'a' else ser.find('a') or ser
                                    href = a.get('href') if a and a.get('href') else None
                                    if href:
                                        mid = re.search(r'/series/(\d+)', href)
                                        if mid:
                                            series_id = mid.group(1)
                                # try to extract number in title on detail page too
                                if not series_number:
                                    span = bs.select_one('span.darkGreyText')
                                    if span:
                                        mnum = re.search(r'#\s*(\d+)', span.get_text())
                                        if mnum:
                                            series_number = mnum.group(1)
                        except Exception:
                            LOG.debug("Failed to fetch book page to extract series id")

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
                    'genres': None
                }

                # Save to DB
                success = self.db.save_book(book, shelves=[shelf_name])
                if success:
                    self.db.add_history(action='fetch_shelf_row', book_id=gid, title=title_clean, status='fetched', meta={'shelf': shelf_name})
                books.append(book)
            except Exception:
                LOG.exception("Failed to parse/save row")
                continue

        return books, has_next

    def _score_candidate(self, candidate, book):
        """
        Very small scoring:
          +50 if isbn matches any (isbn/isbn13/asin)
          +40 if title exact match (case-insensitive)
          +30 if author contains author_first
          +20 fuzzy substring matches
        Candidate is expected to contain title, author, isbn, format, id (md5)
        """
        score = 0
        ctitle = (candidate.get('title') or '').lower()
        cauthor = (candidate.get('author') or '').lower()
        btitle = (book.get('title_clean') or book.get('title') or '').lower()
        bauthor = (book.get('author_first') or book.get('author') or '').lower()

        # ISBN
        for idk in ('isbn','isbn13','asin'):
            if book.get(idk) and candidate.get(idk) and str(book.get(idk)) == str(candidate.get(idk)):
                score += 50

        if ctitle == btitle and ctitle:
            score += 40
        elif btitle and btitle in ctitle:
            score += 20

        if bauthor and bauthor in cauthor:
            score += 30

        return score

    def _search_backend_and_queue(self, book):
        """
        Save the search request JSON, call CWA or backend search and pick best match to queue.
        """
        q = f"{book.get('title_clean') or book.get('title', '')} {book.get('author_first') or book.get('author','')}".strip()
        req_obj = {'query': q, 'filters': {'format': [], 'lang': []}, 'timestamp': datetime.utcnow().isoformat()}
        fname = self.search_requests_dir / f"search_{book['goodreads_id']}_{int(time.time())}.json"
        try:
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(req_obj, f, ensure_ascii=False, indent=2)
        except Exception:
            LOG.exception("Failed to write search request")

        # Prefer using an in-process CWA client if available
        try:
            from src.api.cwa_client import CWAClient
            c = CWAClient(self.config.get('cwa_api_url'), self.config.get('cwa_username'), self.config.get('cwa_password'), str(self.cache_dir))
            results = c.search_books(q, {})
        except Exception:
            results = None

        # fallback to external HTTP search API if configured
        if results is None:
            try:
                url = self.download_backend_url.rstrip("/") + "/api/search"
                r = requests.get(url, params={"query": q}, timeout=15)
                if r.status_code == 200:
                    results = r.json()
                else:
                    results = []
            except Exception:
                LOG.exception("Backend search failed")
                results = []

        # Score and pick best
        best = None
        best_score = -1
        for cand in results or []:
            try:
                sc = self._score_candidate(cand, book)
                cand['_score'] = sc
                if sc > best_score:
                    best_score = sc
                    best = cand
            except Exception:
                continue

        # Save top candidates for debugging
        try:
            cand_fname = self.search_requests_dir / f"candidates_{book['goodreads_id']}_{int(time.time())}.json"
            with open(cand_fname, "w", encoding="utf-8") as f:
                json.dump({"query": q, "candidates": results, "best_score": best_score}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        if not best:
            LOG.info("No candidate found for %s", book.get('title'))
            return False, None

        # Save chosen candidate metadata to book.json_details and update DB
        try:
            self.db.update_book(book['goodreads_id'], {"json_details": best})
        except Exception:
            pass

        # Now queue download (call backend)
        try:
            # If CWA client method exists, use it
            if 'c' in locals() and hasattr(c, 'queue_book'):
                ok = c.queue_book(best.get('id') or best.get('md5') or best.get('hash'))
                return ok, best
            # else call HTTP download endpoint on backend
            url = self.download_backend_url.rstrip("/") + "/api/download"
            params = {"id": best.get('id') or best.get('md5') or best.get('hash')}
            r = requests.get(url, params=params, timeout=10)
            if r.status_code in (200, 201):
                return True, best
            LOG.warning("Backend download API returned %s", r.status_code)
            return False, best
        except Exception:
            LOG.exception("Failed to queue download for candidate")
            return False, best

    def get_goodreads_books_from_shelf(self, shelf_name="to-read", fetch_details=False):
        LOG.info("Fetching books from Goodreads shelf='%s' user=%s", shelf_name, self.user_id)
        page_index = 1
        collected = []
        drv = None
        if self._selenium_available:
            drv = self.driver or self.init_driver_if_possible()
        if drv:
            try:
                self.login_selenium_with_cookies()
            except Exception:
                pass

            base_shelf = f"{self.base_url}/review/list/{self.user_id}?utf8=✓&shelf=%23ALL%23&per_page={self.per_page}"
            while page_index <= self.max_pages:
                url = f"{base_shelf}&page={page_index}"
                LOG.info("Selenium navigating to shelf page %s", url)
                try:
                    drv.get(url)
                    time.sleep(1.0)
                    self.ensure_all_columns_visible()
                    try:
                        WebDriverWait(drv, 10).until(
                            lambda d: d.find_elements(By.CSS_SELECTOR, "tbody#booksBody tr, tr.bookalike, table#books tr")
                        )
                    except TimeoutException:
                        LOG.debug("Timed out waiting for shelf table; will parse available HTML")
                    html = drv.page_source
                except Exception:
                    LOG.exception("Selenium fetch error; falling back to requests for this page")
                    # try fallback
                    session = self._load_cookies_into_requests()
                    r = session.get(url, timeout=30)
                    html = r.text if r.status_code == 200 else ""
                page_books, has_next = self._parse_shelf_html_and_save(html, shelf_name, fetch_details=fetch_details)
                collected.extend(page_books)
                LOG.info("Page %d: collected %d books (cumulative %d)", page_index, len(page_books), len(collected))
                if not has_next:
                    break
                page_index += 1
            # save cookies and close
            self._save_cookies_from_selenium()
            self.close_driver()

        # If nothing collected or Selenium unavailable, fallback to requests
        if not collected:
            try:
                session = self._load_cookies_into_requests()
                base_shelf = f"{self.base_url}/review/list/{self.user_id}"
                params = {"utf8": "✓", "shelf": "#ALL#", "per_page": str(self.per_page)}
                page_index = 1
                while page_index <= self.max_pages:
                    params['page'] = str(page_index)
                    full = f"{base_shelf}?{urlencode(params)}"
                    LOG.info("Requests fetching %s", full)
                    r = session.get(full, timeout=30)
                    if r.status_code != 200:
                        LOG.warning("Requests fetch returned %s", r.status_code)
                        break
                    page_books, has_next = self._parse_shelf_html_and_save(r.text, shelf_name, fetch_details=fetch_details)
                    collected.extend(page_books)
                    LOG.info("Page %d: collected %d books (cumulative %d)", page_index, len(page_books), len(collected))
                    if not has_next:
                        break
                    page_index += 1
            except Exception:
                LOG.exception("Requests shelf fetch failed")

        LOG.info("Total books collected from shelf view: %d", len(collected))
        return collected

    def run_full_sync_and_queue_downloads(self, shelf_name="to-download"):
        """
        High-level method: fetch shelf, then for each book that is in 'to-download' shelves,
        perform search+score and queue best candidate.
        """
        books = self.get_goodreads_books_from_shelf(shelf_name, fetch_details=False)
        for b in books:
            try:
                # Only attempt downloads for books that have 'to-download' shelf or user-intent
                if b.get('shelves') and 'to-download' in b.get('shelves'):
                    ok, candidate = self._search_backend_and_queue(b)
                    self.db.add_history(action='queue_download', book_id=b['goodreads_id'], title=b.get('title_clean'), status='queued' if ok else 'failed', meta={'candidate': candidate})
            except Exception:
                LOG.exception("Failed to process download for %s", b.get('goodreads_id'))

