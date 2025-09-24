#!/usr/bin/env python3
# src/main.py

import os
import sys
from src.utils.config_loader import load_config
from src.utils.logger import setup_logger
from src.sync_logic import orchestrate_sync

def main():
    # Load config (even if not strictly needed for orchestrate_sync)
    config = load_config()

    # Setup logger (stdout-only, Docker-native)
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger = setup_logger(log_level)

    logger.info("🚀 ebooks-manager orchestrator starting (one-shot mode)")
    try:
        orchestrate_sync()
        logger.info("✅ Orchestrator sync completed")
    except Exception as e:
        logger.exception("❌ Orchestrator sync failed: %s", e)
        sys.exit(1)

if __name__ == "__main__":
    main()
