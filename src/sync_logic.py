#!/usr/bin/env python3
# src/sync_logic.py

"""
Core sync orchestration logic for ebooks-manager.

- Scrapes Goodreads shelves and stores results in DB.
- Queues downloads through the CWA downloader API.
- Updates downloads + history in DB for progress tracking.
"""

import logging
import asyncio
import os
from typing import Dict, Any, List, Optional

import httpx

from src.utils.config_loader import load_config
from src.utils.database import Database
from src.scrapers.goodreads import GoodreadsScraper

LOG = logging.getLogger(__name__)


class SyncOrchestrator:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or load_config()
        self.db = Database(self.config.get("database_path", "/app/data/databases/goodreads.db"))
        self.http = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        try:
            await self.http.aclose()
        except Exception:
            pass
        try:
            self.db.close()
        except Exception:
            pass

    async def sync_shelf(self, shelf: str, limit: int = 200) -> List[Dict[str, Any]]:
        """Fetch books from Goodreads shelf and persist to DB."""
        LOG.info("🔄 Syncing shelf: %s", shelf)
        scraper = GoodreadsScraper(self.config)
        books = scraper.get_goodreads_books_from_shelf(shelf, False, limit)

        results = []
        for b in books:
            ok = self.db.save_book(b, shelves=[shelf])
            if ok:
                self.db.add_history("sync", b.get("goodreads_id"), b.get("title"), "synced", {"shelf": shelf})
                results.append(b)
            else:
                LOG.warning("Failed to save book %s", b.get("goodreads_id"))
        LOG.info("✅ Shelf %s sync complete (%d books)", shelf, len(results))
        return results

    async def queue_download(self, goodreads_id: str, candidate_id: str, meta: Optional[Dict[str, Any]] = None) -> bool:
        """Queue a download via CWA and persist in DB."""
        base = self.config.get("cwa_api_url") or os.getenv("CWA_API_URL")
        user = self.config.get("cwa_username") or os.getenv("CWA_USERNAME")
        pw = self.config.get("cwa_password") or os.getenv("CWA_PASSWORD")

        if not base:
            LOG.error("❌ No CWA backend configured, cannot queue download")
            return False

        url = f"{base.rstrip('/')}/api/download"
        try:
            r = await self.http.get(url, params={"id": candidate_id, "priority": 0}, auth=(user, pw) if user and pw else None)
            if r.is_success:
                self.db.create_download(goodreads_id, candidate_id, status="queued", meta=meta)
                self.db.add_history("queue", goodreads_id, None, "queued", {"candidate_id": candidate_id})
                LOG.info("📥 Queued candidate %s for book %s", candidate_id, goodreads_id)
                return True
            else:
                LOG.error("Failed to queue candidate %s: %s %s", candidate_id, r.status_code, r.text)
                self.db.mark_download_failed(candidate_id, f"{r.status_code}: {r.text}")
                return False
        except Exception as e:
            LOG.exception("❌ Error queuing candidate %s", candidate_id)
            self.db.mark_download_failed(candidate_id, str(e))
            return False

    async def update_download_progress(self, candidate_id: str, progress: int, status: str = "downloading"):
        """Update a download's progress in DB."""
        self.db.update_download(candidate_id, status=status, progress=progress)

    async def mark_download_complete(self, candidate_id: str, goodreads_id: Optional[str] = None, title: Optional[str] = None):
        """Mark a download as completed in DB and history."""
        self.db.update_download(candidate_id, status="completed", progress=100)
        self.db.add_history("download", goodreads_id, title, "completed", {"candidate_id": candidate_id})
        LOG.info("✅ Download complete for candidate %s", candidate_id)


async def orchestrate_sync():
    """Main entrypoint for periodic sync (scheduler)."""
    cfg = load_config()
    orch = SyncOrchestrator(cfg)
    try:
        shelves = cfg.get("shelves_to_sync") or ["to-read", "to-download"]
        for shelf in shelves:
            await orch.sync_shelf(shelf, limit=200)
    finally:
        await orch.close()
