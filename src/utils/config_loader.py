#!/usr/bin/env python3
# src/utils/config_loader.py
import json
import os
from pathlib import Path
from typing import Dict, Any

CONFIG_DEFAULT_PATH = "/app/config/config.json"

# Whitelist keys safe to broadcast (everything else is redacted)
SAFE_KEYS = {
    "cwa_api_url",
    "database_path",
    "cache_dir",
    "log_file",
    "history_file",
    "goodreads_user_id",
    "auto_queue_score_threshold",
}

def load_config(config_path: str = CONFIG_DEFAULT_PATH) -> Dict[str, Any]:
    """
    Load config from JSON file, override with env vars, and apply defaults.
    """
    cfg: Dict[str, Any] = {}

    # Load from file if present
    path = Path(config_path)
    if path.exists():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise Exception(f"Invalid JSON in {config_path}: {e}")

    # Apply defaults if missing
    cfg.setdefault("database_path", "/app/data/databases/goodreads.db")
    cfg.setdefault("cache_dir", "/app/data/cache")
    cfg.setdefault("log_file", "/app/logs/sync_log.txt")
    cfg.setdefault("history_file", "/app/logs/history.json")
    cfg.setdefault("auto_queue_score_threshold", 600)

    # Override with environment variables if set
    overrides = {
        "goodreads_username": os.getenv("GOODREADS_USERNAME"),
        "goodreads_password": os.getenv("GOODREADS_PASSWORD"),
        "goodreads_user_id": os.getenv("GOODREADS_USER_ID"),
        "cwa_api_url": os.getenv("CWA_API_URL"),
        "cwa_username": os.getenv("CWA_USERNAME"),
        "cwa_password": os.getenv("CWA_PASSWORD"),
    }
    for k, v in overrides.items():
        if v is not None:
            cfg[k] = v

    return cfg

def safe_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a safe version of config for broadcasting (e.g., over WebSocket).
    Secrets (passwords/tokens) are redacted.
    """
    safe = {}
    for k, v in cfg.items():
        if k in SAFE_KEYS:
            safe[k] = v
        else:
            safe[k] = "***REDACTED***" if isinstance(v, str) and v else v
    return safe

if __name__ == "__main__":
    cfg = load_config()
    print("Loaded config:", json.dumps(safe_config(cfg), indent=2))
