import logging
import sys
from pathlib import Path
import os

def setup_logger(log_file, level='INFO'):
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch(exist_ok=True)
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Clear handlers
    for h in logging.root.handlers[:]:
        logging.root.removeHandler(h)

    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))
    file_handler.setLevel(log_level)

    st = logging.StreamHandler(sys.stdout)
    st.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    st.setLevel(log_level)

    logging.basicConfig(level=log_level, handlers=[file_handler, st], force=True)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("selenium").setLevel(logging.WARNING)
    return logging.getLogger()
