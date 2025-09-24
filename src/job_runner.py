#!/usr/bin/env python3
# src/job_runner.py

import logging
import os
import asyncio
from apscheduler.schedulers.background import BackgroundScheduler
import httpx

from src.sync_logic import orchestrate_sync, SyncOrchestrator
from src.utils.config_loader import load_config
from src.utils.logger import setup_logger
from src.webui.app import broadcast  # 🔥 use WebUI broadcast function

LOG = logging.getLogger("job_runner")


async def poll_active_downloads(orch: SyncOrchestrator, interval: int = 60):
    """
    Periodically poll CWA API for active downloads, update DB,
    and broadcast real-time progress to WebUI clients.
    """
    base = orch.config.get("cwa_api_url") or os.getenv("CWA_API_URL")
    user = orch.config.get("cwa_username") or os.getenv("CWA_USERNAME")
    pw = orch.config.get("cwa_password") or os.getenv("CWA_PASSWORD")

    if not base:
        LOG.warning("No CWA backend configured, skipping download polling")
        return

    url = f"{base.rstrip('/')}/api/downloads/active"
    client = httpx.AsyncClient(timeout=20.0)

    last_state: dict[str, int] = {}  # track progress by book id

    while True:
        try:
            r = await client.get(url, auth=(user, pw) if user and pw else None)
            if r.is_success:
                data = r.json()
                active = data.get("active_downloads", [])

                for d in active:
                    cid = d.get("id") or d.get("md5") or d.get("book_id")
                    if not cid:
                        continue

                    prog = int(d.get("progress", 0))
                    status = d.get("status", "downloading")

                    # Update DB
                    orch.db.update_download(cid, status=status, progress=prog, meta=d)

                    # Only broadcast if something changed
                    prev_prog = last_state.get(cid)
                    if prev_prog != prog or status != "downloading":
                        last_state[cid] = prog
                        await broadcast({
                            "event": "download_progress",
                            "id": cid,
                            "progress": prog,
                            "status": status,
                            "title": d.get("title"),
                        })

                        LOG.info("📡 Progress update for %s: %s%% %s", cid, prog, status)

            else:
                LOG.warning("Active downloads poll failed: %s %s", r.status_code, r.text[:200])

        except Exception as e:
            LOG.error("Polling error: %s", e, exc_info=True)

        await asyncio.sleep(interval)


def run_jobs():
    LOG.debug("Entering run_jobs function")

    if os.getenv("SCHEDULER_STARTED", "false") == "true":
        LOG.info("Scheduler already running, skipping duplicate start.")
        return
    os.environ["SCHEDULER_STARTED"] = "true"

    try:
        config = load_config()
        logger = setup_logger(os.getenv("LOG_LEVEL", "DEBUG"))

        LOG.info("Starting scheduler for daily sync.")
        scheduler = BackgroundScheduler()
        scheduler.add_job(lambda: asyncio.run(orchestrate_sync()), "interval", hours=24)
        scheduler.start()

        # Start async event loop for active download polling
        orch = SyncOrchestrator(config)

        loop = asyncio.get_event_loop()
        loop.create_task(poll_active_downloads(orch, interval=60))

        LOG.info("Scheduler + polling started successfully")
        loop.run_forever()

    except Exception as e:
        LOG.error("Scheduler failed: %s", str(e), exc_info=True)
        raise


if __name__ == "__main__":
    config = load_config()
    setup_logger(os.getenv("LOG_LEVEL", "DEBUG"))
    LOG.info("Job runner starting")
    run_jobs()
