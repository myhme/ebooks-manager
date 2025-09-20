import logging
from src.scrapers.goodreads import GoodreadsScraper
from src.utils.config_loader import load_config

def orchestrate_sync():
    config = load_config()
    logger = logging.getLogger(__name__)
    try:
        scraper = GoodreadsScraper(config)
        logger.info("Starting Goodreads sync")
        scraper.sync()
        logger.info("Goodreads sync finished")
    except Exception as e:
        logger.exception("Sync failed: %s", e)
