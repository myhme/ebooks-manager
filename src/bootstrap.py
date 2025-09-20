import os
from pathlib import Path
import json
import sys

REQUIRED_CONFIG_KEYS = ['goodreads_username', 'goodreads_password', 'goodreads_user_id', 'cwa_api_url', 'cwa_username', 'cwa_password', 'database_path', 'log_file', 'history_file', 'cache_dir']

def check_and_create_app_files():
    """
    Ensures that all necessary config, log, and data directories and files exist before the main application starts.
    Handles errors gracefully and validates configurations.
    """
    print("Running bootstrap checks...")

    try:
        # --- Define Core Paths (with env overrides) ---
        app_dir = Path(os.getenv('APP_DIR', '/app'))
        config_dir = app_dir / 'config'
        log_dir = app_dir / 'logs'
        data_dir = app_dir / 'data'

        # --- All Directories ---
        dirs_to_create = [
            config_dir,
            log_dir,
            log_dir / 'screenshots',
            data_dir,
            data_dir / 'databases',
            data_dir / 'cache' / 'logins' / 'goodreads',
            data_dir / 'cache' / 'logins' / 'cwa',
            data_dir / 'cache' / 'logins' / 'cwa_book_downloader',
            data_dir / 'cache' / 'search_results' / 'cwa_book_downloader',
            data_dir / 'cache' / 'webpages' / 'goodreads' / 'ebooks',
            data_dir / 'cache' / 'webpages' / 'goodreads' / 'others',
            data_dir / 'cache' / 'webpages' / 'storybook' / 'ebooks',
            data_dir / 'cache' / 'webpages' / 'storybook' / 'others',
            data_dir / 'cache' / 'webpages' / 'hardcover' / 'ebooks',
            data_dir / 'cache' / 'webpages' / 'hardcover' / 'others',
            data_dir / 'cache' / 'webpages' / 'goodreads' / 'shelves',
        ]

        for dir_path in dirs_to_create:
            dir_path.mkdir(parents=True, exist_ok=True)
            print(f"Ensured directory exists: {dir_path}")

        # --- Config File ---
        config_file = config_dir / 'config.json'
        config_template = config_dir / 'config.json.template'

        if not config_file.exists():
            if config_template.exists():
                print(f"'{config_file}' not found. Creating from template.")
                config_file.write_text(config_template.read_text())
            else:
                print(f"'{config_template}' not found. Creating with default values.")
                default_config = {
                    "goodreads_username": "your_goodreads_email",
                    "goodreads_password": "your_goodreads_password",
                    "goodreads_user_id": "your_goodreads_user_id",
                    "cwa_api_url": "http://calibre-web:8084/request/api",
                    "cwa_username": "your_cwa_username",
                    "cwa_password": "your_cwa_password",
                    "database_path": "/app/data/databases/goodreads.db",
                    "log_file": "/app/logs/sync_log.txt",
                    "history_file": "/app/logs/history.json",
                    "cache_dir": "/app/data/cache"
                }
                config_file.write_text(json.dumps(default_config, indent=2))

        # Validate config
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
            missing_keys = [key for key in REQUIRED_CONFIG_KEYS if key not in config]
            if missing_keys:
                raise ValueError(f"Config missing required keys: {missing_keys}")
            print("Config validated successfully.")
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Invalid config: {e}. Please fix '{config_file}'.")
            sys.exit(1)

        # --- Log and History Files ---
        log_file = log_dir / 'sync_log.txt'
        history_file = log_dir / 'history.json'
        db_file = data_dir / 'databases' / 'goodreads.db'

        files_to_touch = [log_file, history_file, db_file]
        for file_path in files_to_touch:
            if not file_path.exists():
                print(f"Creating empty '{file_path.name}'.")
                file_path.touch()

        print("Bootstrap checks complete. All necessary files and directories are present.")
    except Exception as e:
        print(f"Bootstrap failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    check_and_create_app_files()
