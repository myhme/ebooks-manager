from src.utils.config_loader import load_config
from src.utils.logger import setup_logger
from src.sync_logic import orchestrate_sync
import os

def main():
    config = load_config()
    setup_logger(config['log_file'], os.getenv('LOG_LEVEL', 'INFO'))
    orchestrate_sync()

if __name__ == "__main__":
    main()
