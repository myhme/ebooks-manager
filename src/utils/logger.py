#!/usr/bin/env python3
# src/utils/logger.py
import logging
import sys
import os

def setup_logger(level: str = "INFO"):
    """
    Configure application logging for Docker-native use (stdout/stderr only).
    File logging is removed — Docker handles log collection.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Remove existing handlers to avoid duplication
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    # Stream handler (stdout)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(log_level)
    stream_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
    )

    logging.basicConfig(
        level=log_level,
        handlers=[stream_handler],
        force=True  # Ensure reconfiguration
    )

    # Silence noisy third-party libs unless explicitly overridden
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("selenium").setLevel(logging.WARNING)

    logger = logging.getLogger()
    logger.debug(f"Logger initialized at level: {log_level}")
    return logger


if __name__ == "__main__":
    # Example: test logger output
    lvl = os.getenv("LOG_LEVEL", "DEBUG")
    log = setup_logger(lvl)
    log.info("Logger test: info level message")
    log.debug("Logger test: debug level message")
