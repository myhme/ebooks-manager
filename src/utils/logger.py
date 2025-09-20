import logging
import sys
from pathlib import Path
import os

def setup_logger(log_file, level='INFO'):
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Ensure log file is writable
    log_path.touch(exist_ok=True)
    try:
        os.chmod(log_path, 0o664)
        os.chown(log_path, 1000, 1000)  # Match appuser UID/GID
    except PermissionError as e:
        print(f"Warning: Failed to set permissions on {log_path}: {e}")  # Fallback to print

    log_level = getattr(logging, level.upper(), logging.INFO)

    # Remove existing handlers to avoid duplicates
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    # Create handlers
    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    file_handler.flush()  # Ensure immediate flush

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(log_level)
    stream_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

    # Configure root logger
    logging.basicConfig(
        level=log_level,
        handlers=[file_handler, stream_handler],
        force=True  # Force re-configuration
    )
    
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("selenium").setLevel(logging.WARNING)
    
    logger = logging.getLogger()
    logger.debug(f"Logger initialized with file: {log_path}, level: {log_level}")
    return logger
