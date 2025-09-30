#!/usr/bin/env python3
# src/api/booklore_client.py
"""
Booklore API client (async) + matching helpers.

Production-oriented, async client for a Booklore-like API.

Features:
- Async login + refresh token handling
- Automatic token expiry detection by decoding JWT payload (no signature verification)
- Token persistence to disk (atomic writes) optional via `token_cache_file`
- Robust request wrapper with retries, exponential backoff + jitter, and automatic token refresh on 401
- Health check / ping helper
- get_books(...) convenience
- Matching helpers (exact id/isbn, fuzzy title+author) using configurable weights/thresholds
  - Uses rapidfuzz.fuzz.ratio for fuzzy matching (0..100 => normalized to 0..1)
- Configurable via constructor params (base_url, username, password, token cache file)
- Async context manager support (async with BookloreClient(...))

Matching configuration (example):
{
  "goodreads_id": 10000,
  "isbn": 8000,
  "title_exact": 3000,
  "author_exact": 2000,
  "title_fuzzy": 1000,
  "author_fuzzy": 800,
  "threshold": 600
}
"""

from __future__ import annotations
import asyncio
import base64
import json
import logging
import os
import random
import time
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from rapidfuzz import fuzz

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())

# Default matching weights & threshold (can be overridden via matching_config or config file)
DEFAULT_MATCHING = {
    "goodreads_id": 10000,
    "isbn": 8000,
    "title_exact": 3000,
    "author_exact": 2000,
    "title_fuzzy": 1000,
    "author_fuzzy": 800,
    "threshold": 600,
}


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    access_expires_at: Optional[float] = None  # epoch seconds


class BookloreClient:
    """
    Async Booklore API client with token handling + matching helpers.

    Basic usage:
        client = BookloreClient(base_url, username=..., password=..., token_cache_file=...)
        await client.login()
        books = await client.get_books()
        matches = client.match_goodreads_against_booklore(my_gr_books, books)
        await client.close()

    Or:
        async with BookloreClient(...) as client:
            ...
    """

    LOGIN_PATH = "/api/v1/auth/login"
    REFRESH_PATH = "/api/v1/auth/refresh"
    BOOKS_PATH = "/api/v1/books"

    def __init__(
        self,
        base_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        token_cache_file: Optional[str] = None,
        timeout: float = 20.0,
        http_client: Optional[httpx.AsyncClient] = None,
        matching_config: Optional[Dict[str, Any]] = None,
        request_max_attempts: int = 3,
        request_backoff_base: float = 0.5,
    ):
        if not base_url:
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self.username = username or os.getenv("BOOKLORE_USERNAME")
        self.password = password or os.getenv("BOOKLORE_PASSWORD")
        self.token_cache_file = Path(token_cache_file) if token_cache_file else None

        self._tokens: Optional[TokenPair] = None
        self._client_provided = http_client is not None
        limits = httpx.Limits(max_keepalive_connections=10, max_connections=50)
        self._client: httpx.AsyncClient = (
            http_client or httpx.AsyncClient(timeout=timeout, limits=limits)
        )

        # retry/backoff configuration
        self.request_max_attempts = int(request_max_attempts)
        self.request_backoff_base = float(request_backoff_base)

        # matching config: merge default + provided
        mc = dict(DEFAULT_MATCHING)
        if matching_config and isinstance(matching_config, dict):
            # only accept keys present in default mapping
            for k in matching_config:
                if k in mc:
                    mc[k] = matching_config[k]
        else:
            # attempt to read from config.json if present (non-fatal)
            cfg_path = os.getenv("CONFIG_PATH", "/app/config/config.json")
            try:
                if os.path.exists(cfg_path):
                    rawcfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
                    # config section may be under "booklore_matching" or "booklore" or "matching"
                    cand = rawcfg.get("booklore_matching") or rawcfg.get("booklore") or rawcfg.get("matching")
                    if isinstance(cand, dict):
                        for k in cand:
                            if k in mc:
                                mc[k] = cand[k]
            except Exception:
                LOG.debug("No external matching config loaded (or failed to parse)", exc_info=True)
        self.matching_config = mc

        # attempt to load tokens from cache
        if self.token_cache_file and self.token_cache_file.exists():
            try:
                raw = json.loads(self.token_cache_file.read_text(encoding="utf-8"))
                self._tokens = TokenPair(
                    access_token=raw.get("accessToken"),
                    refresh_token=raw.get("refreshToken"),
                    access_expires_at=raw.get("access_expires_at"),
                )
                LOG.debug("Loaded tokens from cache %s", self.token_cache_file)
            except Exception:
                LOG.exception("Failed to read token cache; starting without tokens")

    # ----------------------
    # Utilities: JWT decode / atomic save
    # ----------------------
    def _jwt_payload(self, token: str) -> Dict[str, Any]:
        """Decode JWT payload (no signature verification) and return as dict."""
        try:
            parts = token.split(".")
            if len(parts) < 2:
                return {}
            payload_b64 = parts[1]
            padding = "=" * (-len(payload_b64) % 4)
            payload_b64 += padding
            decoded = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
            return json.loads(decoded.decode("utf-8"))
        except Exception:
            return {}

    def _atomic_write_json(self, path: Path, data: Dict[str, Any]) -> None:
        """Write JSON to `path` atomically using temp file + os.replace."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tf:
                tf.write(json.dumps(data))
                tmpname = tf.name
            os.replace(tmpname, str(path))
        except Exception:
            LOG.exception("Atomic write failed for %s", path)
            try:
                path.write_text(json.dumps(data), encoding="utf-8")
            except Exception:
                LOG.exception("Fallback write also failed for %s", path)

    def _save_tokens_to_cache(self) -> None:
        if not self.token_cache_file or not self._tokens:
            return
        payload = {
            "accessToken": self._tokens.access_token,
            "refreshToken": self._tokens.refresh_token,
            "access_expires_at": self._tokens.access_expires_at,
        }
        self._atomic_write_json(self.token_cache_file, payload)
        LOG.debug("Saved tokens to cache %s", self.token_cache_file)

    def _set_tokens_from_response(self, access_token: str, refresh_token: str) -> None:
        """Set tokens and store expiry if present in JWT payload."""
        expires_at = None
        try:
            payload = self._jwt_payload(access_token)
            exp = payload.get("exp")
            if isinstance(exp, (int, float)):
                expires_at = float(exp)
        except Exception:
            expires_at = None

        self._tokens = TokenPair(access_token=access_token, refresh_token=refresh_token, access_expires_at=expires_at)
        try:
            self._save_tokens_to_cache()
        except Exception:
            LOG.exception("Failed to save tokens to cache")

    # ----------------------
    # Auth: login / refresh / ensure
    # ----------------------
    async def login(self, username: Optional[str] = None, password: Optional[str] = None) -> bool:
        """Perform login and store tokens. Returns True on success."""
        u = username or self.username
        p = password or self.password
        if not u or not p:
            LOG.error("Missing username/password for Booklore login")
            return False
        url = f"{self.base_url}{self.LOGIN_PATH}"
        try:
            r = await self._client.post(url, json={"username": u, "password": p})
            r.raise_for_status()
            data = r.json()
            access = data.get("accessToken")
            refresh = data.get("refreshToken")
            if access and refresh:
                self._set_tokens_from_response(access, refresh)
                LOG.info("Booklore login successful")
                return True
            LOG.error("Login response missing tokens: %s", data)
            return False
        except httpx.HTTPStatusError as e:
            LOG.error("Login failed: %s %s", getattr(e.response, "status_code", None), getattr(e.response, "text", "")[:400])
            return False
        except Exception:
            LOG.exception("Login request failed")
            return False

    async def refresh(self) -> bool:
        """Use stored refresh token to obtain new access/refresh tokens."""
        if not self._tokens or not self._tokens.refresh_token:
            LOG.warning("No refresh token present for Booklore refresh")
            return False
        url = f"{self.base_url}{self.REFRESH_PATH}"
        try:
            r = await self._client.post(url, json={"refreshToken": self._tokens.refresh_token})
            r.raise_for_status()
            data = r.json()
            access = data.get("accessToken")
            refresh = data.get("refreshToken")
            if access and refresh:
                self._set_tokens_from_response(access, refresh)
                LOG.info("Booklore refresh successful")
                return True
            LOG.error("Refresh response missing tokens: %s", data)
            return False
        except httpx.HTTPStatusError as e:
            LOG.warning("Refresh failed: %s %s", getattr(e.response, "status_code", None), getattr(e.response, "text", "")[:400])
            return False
        except Exception:
            LOG.exception("Refresh request failed")
            return False

    async def close(self) -> None:
        """Close internal http client if we created it."""
        try:
            if not self._client_provided:
                await self._client.aclose()
        except Exception:
            LOG.exception("Error closing http client")

    async def _ensure_token(self, force_refresh_if_expiring_within: int = 60) -> bool:
        """
        Ensure we have a valid access token.
        If missing: try login.
        If token has 'exp' and is expiring soon: try refresh, otherwise login.
        """
        now = time.time()
        if not self._tokens or not getattr(self._tokens, "access_token", None):
            LOG.debug("No access token: attempting login")
            return await self.login()

        if self._tokens.access_expires_at:
            # refresh if expiring within the window
            if self._tokens.access_expires_at < (now + force_refresh_if_expiring_within):
                LOG.info("Access token expiring soon; attempting refresh")
                ok = await self.refresh()
                if ok:
                    return True
                LOG.info("Refresh failed; attempting fresh login")
                return await self.login()
        return True

    # ----------------------
    # HTTP request wrapper with retries/backoff and auth handling
    # ----------------------
    async def request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        headers: Optional[dict] = None,
        retry_on_401: bool = True,
        max_attempts: Optional[int] = None,
    ) -> Tuple[bool, int, Any]:
        """
        Make an HTTP request to the Booklore API.
        Retries on network / server errors with backoff. Will try refresh/login once on 401 if requested.

        Returns: (ok: bool, status_code: int, parsed_json_or_text)
        """
        # Ensure token state first (this may login if no token available)
        await self._ensure_token()
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = dict(headers or {})

        if self._tokens and self._tokens.access_token:
            headers["Authorization"] = f"Bearer {self._tokens.access_token}"

        attempts = int(max_attempts or self.request_max_attempts)
        backoff_base = float(self.request_backoff_base)
        last_exc: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                resp = await self._client.request(method, url, params=params, json=json_body, headers=headers)

                # handle 401 specially (auth)
                if resp.status_code == 401 and retry_on_401:
                    LOG.debug("401 from Booklore; attempting refresh/login and retry (attempt %d/%d)", attempt, attempts)
                    # first try refresh then login
                    if await self.refresh() or await self.login():
                        # update header and retry immediately once
                        if self._tokens and self._tokens.access_token:
                            headers["Authorization"] = f"Bearer {self._tokens.access_token}"
                        resp = await self._client.request(method, url, params=params, json=json_body, headers=headers)
                    else:
                        # return 401 content if JSON else text
                        try:
                            content_type = resp.headers.get("content-type", "")
                            if "application/json" in content_type:
                                return False, resp.status_code, resp.json()
                        except Exception:
                            pass
                        return False, resp.status_code, resp.text

                # For >=500 treat as transient and retry
                if 500 <= resp.status_code < 600 and attempt < attempts:
                    LOG.warning("Server error from Booklore (%s); attempt %d/%d", resp.status_code, attempt, attempts)
                    sleep_for = backoff_base * (2 ** (attempt - 1)) + random.uniform(0, 0.1 * backoff_base)
                    await asyncio.sleep(sleep_for)
                    continue

                # parse JSON when possible
                content_type = resp.headers.get("content-type", "")
                if "application/json" in content_type:
                    try:
                        return True, resp.status_code, resp.json()
                    except Exception:
                        return False, resp.status_code, resp.text

                # else return raw text
                return True, resp.status_code, resp.text

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.WriteError) as e:
                last_exc = e
                LOG.warning("Network error on request to %s (attempt %d/%d): %s", url, attempt, attempts, e)
                if attempt < attempts:
                    sleep_for = backoff_base * (2 ** (attempt - 1)) + random.uniform(0, 0.2 * backoff_base)
                    await asyncio.sleep(sleep_for)
                    continue
                LOG.exception("Max attempts reached for %s", url)
                return False, 0, f"network_error: {e}"
            except Exception:
                LOG.exception("Unexpected error during request to %s", url)
                return False, 0, "request_failed"

        if last_exc:
            return False, 0, f"network_error: {last_exc}"
        return False, 0, "unknown_failure"

    # ----------------------
    # Domain helpers
    # ----------------------
    async def ping(self, quick_path: str = None) -> bool:
        """
        Lightweight health check. Attempts to fetch a tiny resource.
        Uses '/api/v1/books?withDescription=false' by default (quick).
        Returns True if server responds 2xx.
        """
        try:
            p = quick_path or f"{self.BOOKS_PATH}?withDescription=false"
            ok, status, _ = await self.request("GET", p, retry_on_401=False)
            return ok and (200 <= status < 300)
        except Exception:
            return False

    async def get_books(self, with_description: bool = True, params: Optional[dict] = None) -> List[Dict[str, Any]]:
        """
        Fetch list of books from Booklore.

        Params:
          - with_description: maps to query param 'withDescription'
          - params: dict of additional query params (merged)

        Returns:
          - list of book dictionaries, or empty list on error
        """
        p = params.copy() if params else {}
        p["withDescription"] = str(bool(with_description)).lower()
        ok, code, data = await self.request("GET", self.BOOKS_PATH, params=p)
        if not ok or code >= 400:
            LOG.error("Failed to fetch books from Booklore: %s %s", code, data)
            return []
        # data may be a list, or a wrapper dict with "books"
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "books" in data and isinstance(data["books"], list):
            return data["books"]
        return []

    # ----------------------
    # Matching helpers
    # ----------------------
    @staticmethod
    def _normalize(s: Optional[str]) -> str:
        """Normalize strings: lowercase, strip punctuation-ish characters, remove diacritics and collapse whitespace."""
        if not s:
            return ""
        s = unicodedata.normalize("NFKD", str(s))
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        s = s.lower().strip()
        for ch in ('"', "'", ":", ";", ",", ".", "!", "?", "/", "\\", "(", ")", "[", "]", "{", "}", "&", "-", "_", "—", "–"):
            s = s.replace(ch, " ")
        return " ".join(s.split())

    @staticmethod
    def best_string_match(a: str, b: str) -> float:
        """
        Return similarity 0..1 using rapidfuzz (normalized strings).
        """
        an = BookloreClient._normalize(a)
        bn = BookloreClient._normalize(b)
        if not an or not bn:
            return 0.0
        try:
            return float(fuzz.ratio(an, bn)) / 100.0
        except Exception:
            return 0.0

    def _norm_isbn(self, x: Optional[str]) -> str:
        """Return digits-only isbn string or empty."""
        if not x:
            return ""
        return "".join(ch for ch in str(x) if ch.isdigit())

    def score_candidate_match(self, gr_book: Dict[str, Any], bl_book: Dict[str, Any], weights: Optional[Dict[str, float]] = None) -> float:
        """
        Score how well a Booklore book matches a Goodreads book using weights.

        Returns:
            float (higher == better). Very large score returned for exact goodreads id or isbn matches.
        """
        w = weights or self.matching_config or DEFAULT_MATCHING

        def W(k: str, default: float = 0.0) -> float:
            try:
                return float(w.get(k, DEFAULT_MATCHING.get(k, default)))
            except Exception:
                return float(DEFAULT_MATCHING.get(k, default))

        md = bl_book.get("metadata") or {}

        # 1) goodreads id exact match -> immediate high score
        gr_id = str(gr_book.get("goodreads_id") or gr_book.get("goodreadsId") or "")
        bl_gr = str(md.get("goodreadsId") or md.get("goodreads_id") or "")
        if gr_id and bl_gr and gr_id == bl_gr:
            return max(W("goodreads_id", 10000.0), 10000.0)

        # 2) ISBN exact match -> immediate very high score
        gr_isbn = self._norm_isbn(gr_book.get("isbn") or gr_book.get("isbn13") or gr_book.get("isbn10") or "")
        bl_isbn = self._norm_isbn(md.get("isbn13") or md.get("isbn10") or md.get("isbn") or "")
        if gr_isbn and bl_isbn and gr_isbn == bl_isbn:
            return max(W("isbn", 8000.0), 8000.0)

        # 3) fuzzy title + author scoring
        gr_title = str(gr_book.get("title") or gr_book.get("title_clean") or "")
        bl_title = str(md.get("title") or bl_book.get("title") or "")
        title_ratio = self.best_string_match(gr_title, bl_title)

        gr_author = str(gr_book.get("author") or gr_book.get("author_first") or gr_book.get("authors") or "")
        bl_authors = md.get("authors") or []
        bl_author_join = " ".join(bl_authors) if isinstance(bl_authors, (list, tuple)) else str(bl_authors or "")
        author_ratio = self.best_string_match(gr_author, bl_author_join)

        score = 0.0
        # title exact bump
        if title_ratio >= 0.98:
            score += W("title_exact", 3000.0)
        else:
            score += title_ratio * W("title_fuzzy", 1000.0)

        # author importance
        if author_ratio >= 0.95:
            score += W("author_exact", 2000.0)
        else:
            score += author_ratio * W("author_fuzzy", 800.0)

        return score

    def find_best_match_for_book(self, gr_book: Dict[str, Any], bl_books: List[Dict[str, Any]], threshold: Optional[float] = None) -> Tuple[Optional[Dict[str, Any]], float]:
        """
        Iterate candidates and return (best_book, best_score) if best_score >= threshold,
        otherwise (None, best_score).
        """
        best_score = -1.0
        best = None
        for b in bl_books:
            s = self.score_candidate_match(gr_book, b)
            if s > best_score:
                best_score = s
                best = b
        th = threshold if threshold is not None else float(self.matching_config.get("threshold", DEFAULT_MATCHING["threshold"]))
        if best_score >= th:
            return best, best_score
        return None, best_score

    def match_goodreads_against_booklore(self, goodreads_books: List[Dict[str, Any]], booklore_books: List[Dict[str, Any]], threshold: Optional[float] = None) -> List[Dict[str, Any]]:
        """
        For each Goodreads book, find best Booklore candidate and return list of:
            {"goodreads": gr_book, "match": matched_bl_book | None, "score": float, "reason": str}
        Reasons: "id", "isbn", "fuzzy", "none"
        """
        out: List[Dict[str, Any]] = []
        # index Booklore books by goodreadsId for quick exact matches
        bl_by_grid: Dict[str, List[Dict[str, Any]]] = {}
        for b in booklore_books:
            md = b.get("metadata") or {}
            gr_id = str(md.get("goodreadsId") or md.get("goodreads_id") or "")
            if gr_id:
                bl_by_grid.setdefault(gr_id, []).append(b)

        th = threshold if threshold is not None else float(self.matching_config.get("threshold", DEFAULT_MATCHING["threshold"]))

        for g in goodreads_books:
            gr_id = str(g.get("goodreads_id") or g.get("goodreadsId") or "")
            matched = None
            score = 0.0
            reason = "none"

            # 1) try direct goodreads id match
            if gr_id and gr_id in bl_by_grid:
                candidates = bl_by_grid[gr_id]
                best = None
                best_score = -1.0
                for c in candidates:
                    s = self.score_candidate_match(g, c)
                    if s > best_score:
                        best_score = s
                        best = c
                matched = best
                score = best_score
                reason = "id"

            # 2) overall best match if not matched by id
            if not matched:
                candidate, s = self.find_best_match_for_book(g, booklore_books, threshold=th)
                matched = candidate
                score = s
                reason = "fuzzy" if candidate else "none"

            # 3) if matched but not via immediate isbn/goodreads_id scoring, refine reason if isbn equal
            if matched and score < float(self.matching_config.get("goodreads_id", DEFAULT_MATCHING["goodreads_id"])):
                md_match = matched.get("metadata") or {}
                gr_isbn = self._norm_isbn(g.get("isbn") or g.get("isbn13") or g.get("isbn10") or "")
                bl_isbn = self._norm_isbn(md_match.get("isbn13") or md_match.get("isbn10") or md_match.get("isbn") or "")
                if gr_isbn and bl_isbn and gr_isbn == bl_isbn:
                    reason = "isbn"

            out.append({"goodreads": g, "match": matched, "score": score, "reason": reason})
        return out

    # ----------------------
    # Context manager
    # ----------------------
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()


# ----------------------
# CLI / example usage (quick test)
# ----------------------
async def _example_run():
    """
    Quick test run. Uses BOOKLORE_API_URL, BOOKLORE_USERNAME, BOOKLORE_PASSWORD env vars if present.
    """
    base = os.getenv("BOOKLORE_API_URL", "http://book.com:8080")
    username = os.getenv("BOOKLORE_USERNAME", "docker")
    password = os.getenv("BOOKLORE_PASSWORD", "docker")
    token_cache = os.getenv("BOOKLORE_TOKEN_CACHE", "/app/data/cache/booklore_tokens.json")

    # optional: load matching config from /app/config/config.json if present
    cfg_path = os.getenv("CONFIG_PATH", "/app/config/config.json")
    matching_cfg = None
    try:
        if os.path.exists(cfg_path):
            raw = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
            matching_cfg = raw.get("booklore_matching") or raw.get("booklore") or raw.get("matching")
    except Exception:
        LOG.debug("No config file loaded for matching_cfg")

    client = BookloreClient(base_url=base, username=username, password=password, token_cache_file=token_cache, matching_config=matching_cfg)

    ok = await client.login()
    if not ok:
        LOG.error("Login failed; aborting example")
        await client.close()
        return

    reachable = await client.ping()
    print("Ping OK:", reachable)

    booklore_books = await client.get_books(with_description=True)
    print(f"Fetched {len(booklore_books)} books from Booklore")

    sample_goodreads = [
        {"goodreads_id": "30134847", "title": "Waking Gods", "author": "Sylvain Neuvel", "isbn": "9781101886724"},
        {"goodreads_id": "99999999", "title": "Some Random Title", "author": "Nobody", "isbn": ""},
    ]

    report = client.match_goodreads_against_booklore(sample_goodreads, booklore_books, threshold=None)
    for r in report:
        g = r["goodreads"]
        matched = r["match"]
        print("GR:", g.get("title"), "-> MATCH:", (matched.get("metadata")["title"] if matched else None), "score:", r["score"], "reason:", r["reason"])

    await client.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_example_run())
