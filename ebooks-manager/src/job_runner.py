import logging
import os
import time
from apscheduler.schedulers.background import BackgroundScheduler
from src.sync_logic import orchestrate_sync
from src.utils.config_loader import load_config
from src.utils.logger import setup_logger

def run_jobs():
    config = load_config()
    setup_logger(config['log_file'], os.getenv('LOG_LEVEL', 'INFO'))
    logger = logging.getLogger(__name__)
    if os.getenv('SCHEDULER_STARTED', 'false') == 'true':
        logger.info("Scheduler already started. Exiting duplicate.")
        return
    os.environ['SCHEDULER_STARTED'] = 'true'
    scheduler = BackgroundScheduler()
    # daily run, can be customized via env vars in future
    scheduler.add_job(orchestrate_sync, 'interval', hours=24, next_run_time=None)
    scheduler.start()
    logger.info("Scheduler started.")
    try:
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()

if __name__ == "__main__":
    run_jobs()
