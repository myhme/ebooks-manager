import logging
from src.scrapers.goodreads import GoodreadsScraper
from src.scrapers.hardcover import HardcoverScraper
from src.scrapers.storygraph import StorygraphScraper
from src.utils.config_loader import load_config

def orchestrate_sync():
    config = load_config()
    logger = logging.getLogger()

    # Initialize scrapers
    goodreads_scraper = GoodreadsScraper(config)
    hardcover_scraper = HardcoverScraper(config)
    storygraph_scraper = StorygraphScraper(config)

    # Run Goodreads sync
    logger.info("Starting Goodreads sync")
    try:
        goodreads_scraper.sync()
        logger.info("Goodreads sync completed")
    except Exception as e:
        logger.error(f"Goodreads sync failed: {e}", exc_info=True)

    # Placeholder for other scrapers
    logger.info("Hardcover and Storygraph sync not implemented yet")
    # hardcover_scraper.sync()
    # storygraph_scraper.sync()
