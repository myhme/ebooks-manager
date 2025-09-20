import requests
import json
from pathlib import Path
import time
import logging

class CWAClient:
    def __init__(self, api_url, username, password, cache_dir):
        self.api_url = api_url
        self.auth = (username, password)
        self.cache_dir = Path(cache_dir) / 'cwa_book_downloader' / 'search_results'
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def search_book(self, query, language='en'):
        try:
            search_url = f"{self.api_url}/search"
            search_data = {'query': query, 'language': language}
            response = requests.post(search_url, json=search_data, auth=self.auth)
            response.raise_for_status()
            results = response.json()
            
            # Cache results
            cache_file = self.cache_dir / f"{query.replace(' ', '_')}.json"
            with open(cache_file, 'w') as f:
                json.dump(results, f)
                
            return results
        except requests.RequestException as e:
            logging.error(f"Search request failed: {e}", exc_info=True)
            return None

    def request_download(self, result_id, book_format='epub'):
        try:
            download_url = f"{self.api_url}/download"
            download_data = {'result_id': result_id, 'format': book_format}
            response = requests.post(download_url, json=download_data, auth=self.auth)
            response.raise_for_status()
            return response.json().get('download_id')
        except requests.RequestException as e:
            logging.error(f"Download request failed: {e}", exc_info=True)
            return None

    def check_download_status(self, download_id, max_attempts=10, wait_seconds=10):
        status_url = f"{self.api_url}/status/{download_id}"
        for _ in range(max_attempts):
            try:
                response = requests.get(status_url, auth=self.auth)
                response.raise_for_status()
                status = response.json().get('status')
                if status == 'success':
                    return True
                elif status == 'failure':
                    return False
                time.sleep(wait_seconds)
            except requests.RequestException as e:
                logging.error(f"Status check failed: {e}", exc_info=True)
                time.sleep(wait_seconds)
        return False
