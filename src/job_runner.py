#!/usr/bin/env python3
# src/job_runner.py

import logging
import os
import asyncio
from apscheduler.schedulers.background import BackgroundScheduler

from src.sync_logic import orchestrate_sync, SyncOrchestrator
from src.utils.config_loader import load_config
from src.utils.logger import setup_logger
from src.webui.app import broadcast
from src.api.booklore_client import BookloreClient
from src.api.cwa_client import CWAClient
from src.utils import database

LOG = logging.getLogger("job_runner")


async def poll_active_downloads(orch: SyncOrchestrator, interval: int = 60):
    """
    Poll CWA API for active downloads, update DB,
    and broadcast progress to WebUI clients.
    Uses CWAClient abstraction.
    """
    if not orch.config.get("cwa_api_url"):
        LOG.warning("⚠️ No CWA backend configured, skipping download polling")
        return

    client = CWAClient(
        base_url=orch.config.get("cwa_api_url"),
        username=orch.config.get("cwa_username"),
        password=orch.config.get("cwa_password"),
        db_path=orch.config.get(
            "cwa_cache_db", "/app/data/databases/cwa_downloader/cache.db"
        ),
        cache_days=int(orch.config.get("cwa_cache_days", 30)),
    )

    last_state: dict[str, int] = {}

    while True:
        try:
            active = client.get_active_downloads()
            if not isinstance(active, list):
                LOG.warning("Unexpected active_downloads format: %s", active)
                active = []

            for d in active:
                cid = d.get("id") or d.get("md5") or d.get("book_id")
                if not cid:
                    continue

                prog = int(d.get("progress", 0))
                status = d.get("status", "downloading")

                orch.db.update_download(cid, status=status, progress=prog, meta=d)

                prev_prog = last_state.get(cid)
                if prev_prog != prog or status != "downloading":
                    last_state[cid] = prog
                    await broadcast(
                        {
                            "event": "download_progress",
                            "id": cid,
                            "progress": prog,
                            "status": status,
                            "title": d.get("title"),
                        }
                    )
                    LOG.info("📡 Progress update for %s: %s%% %s", cid, prog, status)

        except Exception as e:
            LOG.error("❌ Polling error: %s", e, exc_info=True)

        await asyncio.sleep(interval)


async def reconcile_booklore(cfg: dict):
    """
    Reconcile Goodreads DB with Booklore library:
    - Fetch books from DB
    - Fetch books from Booklore
    - Match them with BookloreClient (config-driven thresholds/weights)
    - Mark downloaded ones in DB
    """
    LOG.info("🔄 Starting Booklore reconcile job...")

    client = BookloreClient(
        base_url=cfg.get("booklore_api_url"),
        username=cfg.get("booklore_username"),
        password=cfg.get("booklore_password"),
        token_cache_file=cfg.get("booklore_token_cache"),
        matching_config=cfg.get("booklore_matching"),
    )

    async with client:
        ok = await client.login()
        if not ok:
            LOG.error("❌ Booklore login failed, skipping reconcile")
            return

        db = database.Database(cfg["database_path"])
        shelves = db.get_shelves()
        goodreads_books = []
        for s in shelves:
            goodreads_books.extend(db.get_books_by_shelf(s["name"]))

        booklore_books = await client.get_books(with_description=True)

        matches = client.match_goodreads_against_booklore(
            goodreads_books,
            booklore_books,
            threshold=cfg.get(
                "auto_queue_score_threshold",
                client.matching_config.get("threshold"),
            ),
        )

        count = 0
        for m in matches:
            if m["match"]:
                db.update_book(
                    str(m["goodreads"]["goodreads_id"]),
                    {"cover_downloaded": 1},
                )
                count += 1

        LOG.info("✅ Reconcile finished: %s/%s books matched", count, len(goodreads_books))


def clear_cwa_cache(cfg: dict):
    """Daily cleanup job for CWA SQLite cache."""
    try:
        client = CWAClient(
            base_url=cfg.get("cwa_api_url"),
            username=cfg.get("cwa_username"),
            password=cfg.get("cwa_password"),
            db_path=cfg.get(
                "cwa_cache_db", "/app/data/databases/cwa_downloader/cache.db"
            ),
            cache_days=int(cfg.get("cwa_cache_days", 30)),
        )
        client.clear_expired_cache()
        LOG.info("🧹 Cleared expired CWA cache entries")
    except Exception as e:
        LOG.error("❌ Cache cleanup failed: %s", e, exc_info=True)


def run_jobs():
    LOG.debug("🚀 Entering run_jobs function")

    if os.getenv("SCHEDULER_STARTED", "false") == "true":
        LOG.info("Scheduler already running, skipping duplicate start.")
        return
    os.environ["SCHEDULER_STARTED"] = "true"

    try:
        config = load_config()
        setup_logger(os.getenv("LOG_LEVEL", "DEBUG"))

        scheduler = BackgroundScheduler()

        # 🔹 Daily Goodreads sync
        scheduler.add_job(
            lambda: asyncio.run(orchestrate_sync()), "interval", hours=24
        )
        LOG.info("📅 Scheduled daily Goodreads sync")

        # 🔹 Booklore reconcile job
        mode = config.get("reconcile_mode", "periodic")
        interval = int(config.get("reconcile_interval_minutes", 1440))

        if mode == "periodic":
            scheduler.add_job(
                lambda: asyncio.run(reconcile_booklore(config)),
                "interval",
                minutes=interval,
            )
            LOG.info("📅 Scheduled Booklore reconcile every %s minutes", interval)
        elif mode == "realtime":
            scheduler.add_job(
                lambda: asyncio.run(reconcile_booklore(config)),
                "interval",
                minutes=5,
            )
            LOG.info("⚡ Realtime mode: Booklore reconcile every 5 minutes")
        else:
            LOG.warning("⚠️ Unknown reconcile_mode=%s, skipping Booklore reconcile", mode)

        # 🔹 Daily CWA cache cleanup
        scheduler.add_job(
            lambda: clear_cwa_cache(config),
            "interval",
            hours=24,
        )
        LOG.info("📅 Scheduled daily CWA cache cleanup")

        scheduler.start()

        # Start async event loop for active download polling
        orch = SyncOrchestrator(config)
        loop = asyncio.get_event_loop()
        loop.create_task(poll_active_downloads(orch, interval=60))

        LOG.info("✅ Scheduler + polling started successfully")
        loop.run_forever()

    except Exception as e:
        LOG.error("❌ Scheduler failed: %s", str(e), exc_info=True)
        raise


if __name__ == "__main__":
    config = load_config()
    setup_logger(os.getenv("LOG_LEVEL", "DEBUG"))
    LOG.info("📌 Job runner starting")
    run_jobs()
