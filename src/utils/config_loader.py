#!/usr/bin/env python3
# src/utils/config_loader.py
"""
Load configuration from /app/config/config.json (by default) and allow overrides
from environment variables. Also provides safe_config() to redact secrets for
broadcasting/logging.
"""

from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Dict, Any

# Default path inside container
CONFIG_DEFAULT_PATH = "/app/config/config.json"

# Keys that are safe to show (everything else will be redacted in safe_config)
SAFE_KEYS = {
    "cwa_api_url",
    "database_path",
    "cache_dir",
    "log_file",
    "history_file",
    "goodreads_user_id",
    "auto_queue_score_threshold",
    "booklore_api_url",
    "reconcile_mode",
    "reconcile_interval_minutes",
    "booklore_matching",  # ✅ safe because it’s just weights
}

# Defaults applied when missing from config file
DEFAULTS: Dict[str, Any] = {
    "database_path": "/app/data/databases/goodreads.db",
    "cache_dir": "/app/data/cache",
    "log_file": "/app/logs/sync_log.txt",
    "history_file": "/app/logs/history.json",
    "auto_queue_score_threshold": 600,
    "goodreads_per_page": 100,
    # Booklore defaults
    "booklore_api_url": os.getenv("BOOKLORE_API_URL", "http://book.com:8080"),
    "booklore_username": os.getenv("BOOKLORE_USERNAME"),
    "booklore_password": os.getenv("BOOKLORE_PASSWORD"),
    "booklore_token_cache": os.getenv("BOOKLORE_TOKEN_CACHE", "/app/data/cache/booklore_tokens.json"),
    # CWA defaults
    "cwa_api_url": os.getenv("CWA_API_URL"),
    "cwa_username": os.getenv("CWA_USERNAME"),
    "cwa_password": os.getenv("CWA_PASSWORD"),
    # reconcile defaults
    "reconcile_mode": os.getenv("RECONCILE_MODE", "periodic"),
    "reconcile_interval_minutes": int(os.getenv("RECONCILE_INTERVAL_MINUTES") or "1440"),
    # ✅ Matching thresholds & weights
    "booklore_matching": {
        "threshold": 600,
        "weights": {
            "goodreads_id": 10000,
            "isbn": 8000,
            "title_exact": 3000,
            "author_exact": 2000,
            "title_fuzzy": 1000,
            "author_fuzzy": 800,
        },
    },
}


def load_config(config_path: str = CONFIG_DEFAULT_PATH) -> Dict[str, Any]:
    """
    Load config from a JSON file, apply defaults, then override with environment
    variables where appropriate.

    Returns a dict of configuration values.
    """
    cfg: Dict[str, Any] = {}

    # Read file if present
    path = Path(config_path)
    if path.exists():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise Exception(f"Invalid JSON in {config_path}: {e}")

    # Apply defaults where missing
    for k, v in DEFAULTS.items():
        if isinstance(v, dict):
            cfg.setdefault(k, {})
            for subk, subv in v.items():
                cfg[k].setdefault(subk, subv)
        else:
            cfg.setdefault(k, v)

    # Apply explicit env overrides
    env_overrides = {
        "goodreads_username": os.getenv("GOODREADS_USERNAME"),
        "goodreads_password": os.getenv("GOODREADS_PASSWORD"),
        "goodreads_user_id": os.getenv("GOODREADS_USER_ID"),
        "cwa_api_url": os.getenv("CWA_API_URL"),
        "cwa_username": os.getenv("CWA_USERNAME"),
        "cwa_password": os.getenv("CWA_PASSWORD"),
        "database_path": os.getenv("DATABASE_PATH"),
        "cache_dir": os.getenv("CACHE_DIR"),
        "log_file": os.getenv("LOG_FILE"),
        "history_file": os.getenv("HISTORY_FILE"),
        "booklore_api_url": os.getenv("BOOKLORE_API_URL"),
        "booklore_username": os.getenv("BOOKLORE_USERNAME"),
        "booklore_password": os.getenv("BOOKLORE_PASSWORD"),
        "booklore_token_cache": os.getenv("BOOKLORE_TOKEN_CACHE"),
        "reconcile_mode": os.getenv("RECONCILE_MODE"),
        "reconcile_interval_minutes": os.getenv("RECONCILE_INTERVAL_MINUTES"),
        # Matching config (env JSON string)
        "booklore_matching": os.getenv("BOOKLORE_MATCHING"),
    }
    for k, v in env_overrides.items():
        if v is not None:
            if k == "reconcile_interval_minutes":
                try:
                    cfg[k] = int(v)
                except Exception:
                    cfg[k] = DEFAULTS["reconcile_interval_minutes"]
            elif k == "booklore_matching":
                try:
                    cfg[k] = json.loads(v)
                except Exception:
                    cfg[k] = DEFAULTS["booklore_matching"]
            else:
                cfg[k] = v

    return cfg


def safe_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a redacted copy of cfg for broadcasting/log display to avoid leaking secrets.
    """
    safe: Dict[str, Any] = {}
    for k, v in cfg.items():
        if k in SAFE_KEYS:
            safe[k] = v
        else:
            if isinstance(v, str) and v:
                safe[k] = "***REDACTED***"
            else:
                safe[k] = v
    return safe


if __name__ == "__main__":
    cfg = load_config()
    print("Loaded config:", json.dumps(safe_config(cfg), indent=2))
