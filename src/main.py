import schedule
import time
import logging
from job_runner import sync_job, load_config

if __name__ == "__main__":
    logging.info("Ebooks Manager Sync Service started.")
    
    sync_interval = 6
    config_valid = False

    # Try to load config, but don't exit if it fails.
    try:
        app_config = load_config()
        sync_interval = app_config.get('sync_interval_hours', 6)
        config_valid = True
        logging.info(f"Configuration loaded. Sync will run every {sync_interval} hours.")
    except Exception as e:
        logging.warning(f"Could not load config: {e}")
        logging.warning("Scheduler will run, but syncs will fail until config is corrected.")

    # Run the job once on startup ONLY if the config was valid
    if config_valid:
        logging.info("Running initial sync on startup...")
        sync_job()
    else:
        logging.warning("Skipping initial sync due to invalid configuration.")

    # Schedule the job regardless of config state.
    # The job itself will handle failing gracefully.
    schedule.every(sync_interval).hours.do(sync_job)

    while True:
        schedule.run_pending()
        time.sleep(60)
