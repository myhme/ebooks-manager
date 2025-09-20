import json
import os
from pathlib import Path

def load_config(config_path='/app/config/config.json'):
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        # Override with environment variables if set
        config['goodreads_username'] = os.getenv('GOODREADS_USERNAME', config.get('goodreads_username'))
        config['goodreads_password'] = os.getenv('GOODREADS_PASSWORD', config.get('goodreads_password'))
        config['goodreads_user_id'] = os.getenv('GOODREADS_USER_ID', config.get('goodreads_user_id'))
        config['cwa_api_url'] = os.getenv('CWA_API_URL', config.get('cwa_api_url'))
        config['cwa_username'] = os.getenv('CWA_USERNAME', config.get('cwa_username'))
        config['cwa_password'] = os.getenv('CWA_PASSWORD', config.get('cwa_password'))
        return config
    except FileNotFoundError:
        raise Exception(f"Configuration file {config_path} not found")
    except json.JSONDecodeError:
        raise Exception(f"Invalid JSON in configuration file {config_path}")
