#!/usr/bin/env python3
# src/bootstrap.py
import os
from pathlib import Path
import json
import sys

REQUIRED_CONFIG_KEYS = [
    "goodreads_username",
    "goodreads_password",
    "goodreads_user_id",
    "cwa_api_url",
    "cwa_username",
    "cwa_password",
    "database_path",
    "log_file",
    "history_file",
    "cache_dir",
]

def check_and_create_app_files():
    """
    Ensure config, logs, and DB files exist before the app starts.
    Creates defaults if missing, validates config.
    """
    print("Running bootstrap checks...")

    try:
        # --- Directories ---
        app_dir = Path(os.getenv("APP_DIR", "/app"))
        config_dir = app_dir / "config"
        log_dir = app_dir / "logs"
        data_dir = app_dir / "data"

        dirs_to_create = [
            config_dir,
            log_dir,
            log_dir / "screenshots",
            data_dir,
            data_dir / "databases",
            # caches
            data_dir / "cache" / "logins" / "goodreads",
            data_dir / "cache" / "logins" / "cwa",
            data_dir / "cache" / "logins" / "cwa_book_downloader",
            data_dir / "cache" / "search_results" / "cwa_book_downloader",
            data_dir / "cache" / "webpages" / "goodreads" / "ebooks",
            data_dir / "cache" / "webpages" / "goodreads" / "others",
            data_dir / "cache" / "webpages" / "storybook" / "ebooks",
            data_dir / "cache" / "webpages" / "storybook" / "others",
            data_dir / "cache" / "webpages" / "hardcover" / "ebooks",
            data_dir / "cache" / "webpages" / "hardcover" / "others",
            data_dir / "cache" / "webpages" / "goodreads" / "shelves",
        ]
        for d in dirs_to_create:
            d.mkdir(parents=True, exist_ok=True)
            print(f"Ensured directory: {d}")

        # --- Config file ---
        config_file = config_dir / "config.json"
        config_template = config_dir / "config.json.template"

        if not config_file.exists():
            if config_template.exists():
                print(f"Config not found. Creating from template: {config_template}")
                config_file.write_text(config_template.read_text())
            else:
                print("Config not found and no template provided. Writing defaults.")
                default_config = {
                    "goodreads_username": "your_goodreads_email",
                    "goodreads_password": "your_goodreads_password",
                    "goodreads_user_id": "your_goodreads_user_id",
                    "cwa_api_url": "http://cwa-downloader:5000/request/api",
                    "cwa_username": "your_cwa_username",
                    "cwa_password": "your_cwa_password",
                    "database_path": str(data_dir / "databases" / "goodreads.db"),
                    "log_file": str(log_dir / "sync_log.txt"),
                    "history_file": str(log_dir / "history.json"),
                    "cache_dir": str(data_dir / "cache"),
                }
                config_file.write_text(json.dumps(default_config, indent=2))

        # --- Validate config ---
        try:
            config = json.loads(config_file.read_text())
        except Exception as e:
            print(f"Invalid config JSON: {e}")
            sys.exit(1)

        # Apply environment overrides
        overrides = {
            "goodreads_username": os.getenv("GOODREADS_USERNAME"),
            "goodreads_password": os.getenv("GOODREADS_PASSWORD"),
            "goodreads_user_id": os.getenv("GOODREADS_USER_ID"),
            "cwa_api_url": os.getenv("CWA_API_URL"),
            "cwa_username": os.getenv("CWA_USERNAME"),
            "cwa_password": os.getenv("CWA_PASSWORD"),
            "database_path": os.getenv("DATABASE_PATH"),
            "log_file": os.getenv("LOG_FILE"),
            "history_file": os.getenv("HISTORY_FILE"),
            "cache_dir": os.getenv("CACHE_DIR"),
        }
        changed = False
        for k, v in overrides.items():
            if v:
                config[k] = v
                changed = True
                print(f"Overrode {k} with ENV")

        if changed:
            config_file.write_text(json.dumps(config, indent=2))

        # Check required keys
        missing = [k for k in REQUIRED_CONFIG_KEYS if not config.get(k)]
        if missing:
            print(f"Config missing required keys: {missing}")
            sys.exit(1)

        print("Config validated ✅")

        # --- Ensure log/history/db files ---
        for f in [
            Path(config.get("log_file", log_dir / "sync_log.txt")),
            Path(config.get("history_file", log_dir / "history.json")),
            Path(config.get("database_path", data_dir / "databases" / "goodreads.db")),
        ]:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.touch(exist_ok=True)
            print(f"Ensured file: {f}")

        print("Bootstrap complete ✅")
    except Exception as e:
        print(f"Bootstrap failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    check_and_create_app_files()
