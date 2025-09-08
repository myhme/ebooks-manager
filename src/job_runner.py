import logging
import json
import os
from sync_logic import BookSyncAutomation
from datetime import datetime

# --- Configuration and Paths ---
LOG_DIR = 'logs'
LOG_FILE = os.path.join(LOG_DIR, 'sync_log.txt')
HISTORY_FILE = os.path.join(LOG_DIR, 'history.json')
CONFIG_FILE = 'config/config.json'

# --- Ensure log directory exists ---
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# --- Logger Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

def load_config():
    """Loads configuration from the JSON file."""
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        required_keys = ['goodreads_user_id', 'storygraph_email', 'storygraph_password']
        if not all(key in config and config[key] and "YOUR_" not in config[key] for key in required_keys):
            raise ValueError("One or more required keys are missing or not set in config.json")
        return config
    except FileNotFoundError:
        logging.error(f"FATAL: Config file not found at {CONFIG_FILE}. Please create it from the template.")
        raise
    except (json.JSONDecodeError, ValueError) as e:
        logging.error(f"FATAL: Error in config file: {e}")
        raise

def update_history(status, books_processed, error_message=None):
    """Updates the task history log."""
    history_entry = {
        'timestamp': datetime.now().isoformat(),
        'status': status,
        'books_processed': books_processed,
        'error': error_message
    }
    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r') as f:
            try:
                history = json.load(f)
            except json.JSONDecodeError:
                history = []
    history.insert(0, history_entry)
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history[:50], f, indent=4)


def sync_job():
    """The main job function. Can be called by scheduler or API."""
    logging.info("="*50)
    logging.info("🚀 Starting Goodreads to StoryGraph sync job...")
    logging.info("="*50)
    
    sync_bot = None
    processed_titles = []
    
    try:
        config = load_config()
        sync_bot = BookSyncAutomation(
            goodreads_user_id=config['goodreads_user_id'],
            storygraph_email=config['storygraph_email'],
            storygraph_password=config['storygraph_password']
        )
        processed_titles = sync_bot.sync_books()
        logging.info("✅ Sync job completed successfully.")
        update_history("Success", processed_titles)

    except Exception as e:
        logging.error(f"❌ Sync job failed: {e}", exc_info=True)
        update_history("Failure", processed_titles, error_message=str(e))
    finally:
        if sync_bot and sync_bot.driver:
            logging.info("Browser is being closed by the sync_logic class.")
        logging.info("="*50)
        logging.info("Sync job finished.")
        logging.info("="*50)
