# src/sync_logic.py
import logging
from src.scrapers.goodreads import GoodreadsScraper
from src.utils.config_loader import load_config
from src.utils.database import Database

LOG = logging.getLogger(__name__)

def orchestrate_sync(shelves=None):
    """
    If shelves is None -> syncs default shelf 'to-download'.
    shelves can be a string or list of shelf names.
    """
    config = load_config()
    db = Database(config['database_path'])
    scraper = GoodreadsScraper(config)
    if not shelves:
        shelves = ['to-download']
    if isinstance(shelves, str):
        shelves = [shelves]

    for shelf in shelves:
        try:
            LOG.info("Starting sync for shelf: %s", shelf)
            books = scraper.get_goodreads_books_from_shelf(shelf)
            if not books:
                LOG.info("No books returned for shelf %s", shelf)
                continue
            for b in books:
                # Save book to DB with shelf association
                db.save_book(b, shelves=[shelf])
                db.add_history(action='fetch_shelf', book_id=b['goodreads_id'], title=b.get('title'), status='fetched', meta={'shelf': shelf})
                # Optionally, kick off cwa request (if enabled)
                # For safety -- only if DRY_RUN is disabled and shelf is 'to-download'
                if config.get('dry_run', False) or (shelf != 'to-download'):
                    continue
                try:
                    # request download via CWA and track
                    results = scraper.cwa_client.search_book(f"{b['title']} {b['author']}")
                    if not results or not results.get('results'):
                        db.add_history(action='search', book_id=b['goodreads_id'], title=b.get('title'), status='no_results')
                        continue
                    best = results['results'][0]
                    # create a download record
                    dl_internal_id = db.create_download(b['goodreads_id'], result_id=str(best.get('id')), download_id=None, status='requested', meta={'result_meta': best})
                    download_id = scraper.cwa_client.request_download(best.get('id'))
                    if download_id:
                        db.update_download(dl_internal_id, status='started', download_id=str(download_id))
                        ok = scraper.cwa_client.check_download_status(download_id)
                        db.update_download(dl_internal_id, status='success' if ok else 'failure')
                        db.add_history(action='download', book_id=b['goodreads_id'], title=b.get('title'), status='success' if ok else 'failure')
                    else:
                        db.update_download(dl_internal_id, status='failed_to_request')
                        db.add_history(action='download', book_id=b['goodreads_id'], title=b.get('title'), status='failed_to_request')
                except Exception:
                    LOG.exception("CWA download failed for %s", b['goodreads_id'])
                    db.add_history(action='download', book_id=b['goodreads_id'], title=b.get('title'), status='error')
        except Exception:
            LOG.exception("Sync for shelf %s failed", shelf)
