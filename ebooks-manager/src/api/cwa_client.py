import requests
import json
from pathlib import Path
import logging
import time

class CWAClient:
    def __init__(self, api_url, username, password, cache_dir):
        self.api_url = api_url
        self.auth = (username, password)
        self.cache_dir = Path(cache_dir) / 'cwa_book_downloader' / 'search_results'
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def search_book(self, query, language='en'):
        try:
            search_url = f"{self.api_url}/search"
            response = requests.post(search_url, json={'query': query, 'language': language}, auth=self.auth, timeout=30)
            response.raise_for_status()
            results = response.json()
            cache_file = self.cache_dir / f"{query.replace(' ', '_')}.json"
            cache_file.write_text(json.dumps(results))
            return results
        except requests.RequestException as e:
            logging.error(f"CWA search failed: {e}", exc_info=True)
            return None

    def request_download(self, result_id, book_format='epub'):
        try:
            download_url = f"{self.api_url}/download"
            response = requests.post(download_url, json={'result_id': result_id, 'format': book_format}, auth=self.auth, timeout=30)
            response.raise_for_status()
            return response.json().get('download_id')
        except requests.RequestException as e:
            logging.error(f"Download request failed: {e}", exc_info=True)
            return None

    def check_download_status(self, download_id, max_attempts=10, wait_seconds=10):
        status_url = f"{self.api_url}/status/{download_id}"
        for _ in range(max_attempts):
            try:
                r = requests.get(status_url, auth=self.auth, timeout=20)
                r.raise_for_status()
                status = r.json().get('status')
                if status == 'success':
                    return True
                if status == 'failure':
                    return False
                time.sleep(wait_seconds)
            except requests.RequestException as e:
                logging.error(f"Status check failed: {e}", exc_info=True)
                time.sleep(wait_seconds)
        return False
