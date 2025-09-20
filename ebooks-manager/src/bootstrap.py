import os
import json
import sys
from pathlib import Path

REQUIRED_CONFIG_KEYS = [
    'goodreads_username', 'goodreads_password', 'goodreads_user_id',
    'cwa_api_url', 'cwa_username', 'cwa_password',
    'database_path', 'log_file', 'history_file', 'cache_dir'
]

def check_and_create_app_files():
    print("Running bootstrap checks...")
    try:
        app_dir = Path(os.getenv('APP_DIR', '/app'))
        config_dir = app_dir / 'config'
        log_dir = app_dir / 'logs'
        data_dir = app_dir / 'data'

        dirs = [
            config_dir,
            log_dir,
            log_dir / 'screenshots',
            data_dir,
            data_dir / 'databases',
            data_dir / 'cache' / 'logins' / 'goodreads',
            data_dir / 'cache' / 'webpages' / 'goodreads' / 'ebooks'
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
            print(f"Ensured directory: {d}")

        config_file = config_dir / 'config.json'
        template = config_dir / 'config.json.template'
        if not config_file.exists():
            if template.exists():
                config_file.write_text(template.read_text())
                print(f"Copied config from template to {config_file}")
            else:
                default = {
                    "goodreads_username": "",
                    "goodreads_password": "",
                    "goodreads_user_id": "",
                    "cwa_api_url": "http://calibre-web:8084/request/api",
                    "cwa_username": "",
                    "cwa_password": "",
                    "database_path": "/app/data/databases/goodreads.db",
                    "log_file": "/app/logs/sync_log.txt",
                    "history_file": "/app/logs/history.json",
                    "cache_dir": "/app/data/cache"
                }
                config_file.write_text(json.dumps(default, indent=2))
                print(f"Wrote default config to {config_file}")

        # Validate config
        try:
            c = json.loads(config_file.read_text())
            missing = [k for k in REQUIRED_CONFIG_KEYS if k not in c or c[k] is None]
            if missing:
                raise ValueError(f"Missing keys: {missing}")
            print("Config validated.")
        except Exception as e:
            print(f"Invalid config: {e}")
            sys.exit(1)

        # Ensure files exist
        for f in [log_dir / 'sync_log.txt', log_dir / 'history.json', data_dir / 'databases' / 'goodreads.db']:
            if not f.exists():
                f.parent.mkdir(parents=True, exist_ok=True)
                f.touch()
                print(f"Created {f}")

        print("Bootstrap checks complete.")
    except Exception as exc:
        print(f"Bootstrap failed: {exc}")
        sys.exit(1)

if __name__ == "__main__":
    check_and_create_app_files()
