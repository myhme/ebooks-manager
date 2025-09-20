import logging
from apscheduler.schedulers.background import BackgroundScheduler
from src.sync_logic import orchestrate_sync
from src.utils.config_loader import load_config
from src.utils.logger import setup_logger
import os
import time

def run_jobs():
    logging.debug("Entering run_jobs function")
    # Prevent multiple scheduler instances
    if os.getenv('SCHEDULER_STARTED', 'false') == 'true':
        logging.info("Scheduler already running, skipping duplicate start.")
        return
    os.environ['SCHEDULER_STARTED'] = 'true'

    try:
        config = load_config()  # Load configuration
        logger = setup_logger(config['log_file'], os.getenv('LOG_LEVEL', 'DEBUG'))
        logging.info("Starting scheduler for daily sync.")
        scheduler = BackgroundScheduler()
        scheduler.add_job(orchestrate_sync, 'interval', hours=24)
        scheduler.start()
        logging.info("Scheduler started successfully")
        # Keep the container running
        while True:
            logging.debug("Scheduler loop running")
            time.sleep(3600)  # Sleep for an hour
    except Exception as e:
        logging.error(f"Scheduler failed: {str(e)}", exc_info=True)
        raise

if __name__ == "__main__":
    config = load_config()  # Load config early
    setup_logger(config['log_file'], os.getenv('LOG_LEVEL', 'DEBUG'))
    logging.info("Job runner starting")
    run_jobs()