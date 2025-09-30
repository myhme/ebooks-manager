#!/usr/bin/env python3
# src/api/cwa_client.py
"""
CWA (Calibre-Web Automated Book Downloader) API client.

Features:
- Wraps all major API endpoints (search, info, download, queue, status, cancel, reorder).
- Handles BasicAuth automatically from config/env.
- Built-in SQLite caching of search results & book info (default 30 days, configurable).
- Retries transient failures with exponential backoff.
- Provides high-level convenience `download_search()` helper.

Caching strategy:
- SQLite DB at /data/databases/cwa_downloader/cache.db
- Tables: search_cache, info_cache
- Cache entries expire after `cache_days` (default 30 days).
"""

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import httpx

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())


class CWAClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        db_path: Union[str, Path] = "/app/data/databases/cwa_downloader/cache.db",
        cache_days: int = 30,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_factor: float = 1.5,
    ):
        raw_base = base_url or os.getenv("CWA_API_URL") or ""
        if not raw_base:
            raise ValueError("CWA_API_URL is not set in config or env")

        # Normalize base_url → always include /request/api
        b = raw_base.rstrip("/")
        if not b.endswith("/request/api"):
            if b.endswith("/request"):
                b = b + "/api"
            else:
                b = b + "/request/api"

        self.base_url = b
        self.username = username or os.getenv("CWA_USERNAME")
        self.password = password or os.getenv("CWA_PASSWORD")

        self.client = httpx.Client(timeout=timeout, auth=(self.username, self.password))
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_ttl = cache_days * 86400  # seconds
        self._init_db()

    # ----------------------
    # DB cache helpers
    # ----------------------
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.commit()

    def _cache_get(self, key: str, type_: str) -> Optional[Any]:
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT data, created_at FROM cache WHERE key=? AND type=?", (key, type_))
            row = cur.fetchone()
            if not row:
                return None
            data, created_at = row
            if now - created_at > self.cache_ttl:
                LOG.debug("Cache expired for %s:%s", type_, key)
                return None
            try:
                return json.loads(data)
            except Exception:
                LOG.warning("Corrupt cache for %s:%s", type_, key)
                return None

    def _cache_put(self, key: str, type_: str, data: Any):
        payload = json.dumps(data)
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "REPLACE INTO cache (key, type, data, created_at) VALUES (?, ?, ?, ?)",
                (key, type_, payload, now),
            )
            conn.commit()

    def clear_expired_cache(self):
        """Remove expired cache rows."""
        cutoff = time.time() - self.cache_ttl
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM cache WHERE created_at < ?", (cutoff,))
            conn.commit()
            LOG.info("Cleared %s expired cache rows", cur.rowcount)

    # ----------------------
    # Internal helpers
    # ----------------------
    def _url(self, path: str) -> str:
        """
        Normalize API paths so we never generate /api/api.
        Caller should pass just 'search', 'info', etc.
        """
        path = path.lstrip("/")
        if path.startswith("api/"):
            path = path[4:]
        return f"{self.base_url}/{path}"

    def _request(self, method: str, path: str, **kwargs) -> Optional[httpx.Response]:
        """Make request with retries and logging."""
        url = self._url(path)
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                r = self.client.request(method, url, **kwargs)
                r.raise_for_status()
                return r
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                last_err = e
                wait = self.backoff_factor ** (attempt - 1)
                LOG.warning("Request failed (%s), retry %s/%s in %.1fs", e, attempt, self.max_retries, wait)
                time.sleep(wait)
        LOG.error("Request to %s failed after %s attempts: %s", url, self.max_retries, last_err)
        return None

    # ----------------------
    # Core API methods
    # ----------------------
    def search_books(self, query: str, **params) -> List[Dict[str, Any]]:
        """Search for books by query. Cached for cache_days."""
        cache_key = f"search:{query}:{json.dumps(params, sort_keys=True)}"
        cached = self._cache_get(cache_key, "search")
        if cached is not None:
            return cached

        r = self._request("GET", "search", params={"query": query, **params})
        if not r:
            return []
        try:
            data = r.json()
        except Exception:
            LOG.error("Invalid JSON from search for %s", query)
            return []
        self._cache_put(cache_key, "search", data)
        return data

    def get_info(self, book_id: str) -> Dict[str, Any]:
        """Get book info by MD5 ID. Cached for cache_days."""
        cache_key = f"info:{book_id}"
        cached = self._cache_get(cache_key, "info")
        if cached is not None:
            return cached

        r = self._request("GET", "info", params={"id": book_id})
        if not r:
            return {}
        try:
            data = r.json()
            self._cache_put(cache_key, "info", data)
            return data
        except Exception:
            LOG.error("Invalid JSON from info for %s", book_id)
            return {}

    def queue_download(self, book_id: str, priority: int = 0) -> Dict[str, Any]:
        r = self._request("GET", "download", params={"id": book_id, "priority": priority})
        return r.json() if r else {}

    def get_status(self) -> Dict[str, Any]:
        r = self._request("GET", "status")
        return r.json() if r else {}

    def get_active_downloads(self) -> List[Dict[str, Any]]:
        r = self._request("GET", "downloads/active")
        return r.json().get("active_downloads", []) if r else []

    def cancel_download(self, book_id: str) -> Dict[str, Any]:
        r = self._request("DELETE", f"download/{book_id}/cancel")
        return r.json() if r else {}

    def set_priority(self, book_id: str, priority: int) -> Dict[str, Any]:
        r = self._request("PUT", f"queue/{book_id}/priority", json={"priority": priority})
        return r.json() if r else {}

    def reorder_queue(self, new_order: Dict[str, int]) -> Dict[str, Any]:
        """Reorder queue with explicit dict of book_id -> priority."""
        r = self._request("POST", "queue/reorder", json={"book_priorities": new_order})
        return r.json() if r else {}

    def get_queue_order(self) -> Dict[str, Any]:
        r = self._request("GET", "queue/order")
        return r.json() if r else {}

    def clear_queue(self) -> Dict[str, Any]:
        r = self._request("DELETE", "queue/clear")
        return r.json() if r else {}

    def local_download(self, book_id: str, out_file: Union[str, Path]) -> Optional[Path]:
        r = self._request("GET", "localdownload", params={"id": book_id})
        if not r:
            return None
        out_path = Path(out_file)
        out_path.write_bytes(r.content)
        return out_path

    # ----------------------
    # Convenience
    # ----------------------
    def download_search(self, query: str, priority: int = 0, **params) -> Optional[Dict[str, Any]]:
        """
        Search for a book and immediately queue first/best result for download.
        Returns queued download response, or None if nothing found.
        """
        results = self.search_books(query, **params)
        if not results:
            LOG.warning("No results for query: %s", query)
            return None

        book = results[0]
        book_id = book.get("id") or book.get("md5")
        if not book_id:
            LOG.error("Search result missing id: %s", book)
            return None

        LOG.info("Queueing download for %s (%s)", book.get("title"), book_id)
        return self.queue_download(book_id, priority=priority)
