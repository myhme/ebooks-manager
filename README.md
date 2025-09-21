# Ebooks Manager — README

A short, practical guide to help you (and future ChatGPT sessions) understand this project, how it’s organized, how to run it, and where the important files live. Use this as the canonical orientation when you return to the project or ask for help.

---

# Overview

**Ebooks Manager** is a self-hosted application that:

- Scrapes your Goodreads shelf(s) (using a Selenium-enabled or `requests` fallback scraper).
- Stores book metadata in a local SQLite database.
- Integrates with a remote download/search backend (e.g. Calibre-Web-Automated (CWA) or the AA-based backend) to search for and queue downloads.
- Provides a Flask web UI to view shelves, queue downloads, and monitor download status.

Key goals of the recent refactor:
- Robust, extensible `Database` that tolerates schema changes and supports pagination (offset).
- Cleaner web UI that reads from the DB (not live scraping), serves downloaded cover images, and includes a downloads page.
- Goodreads scraper improved to use the special `#ALL#` shelf view, a configurable per-page parameter (defaults to 100), pagination, dynamic column mapping, cover downloading, and search-request logging.
- Search requests and candidate results saved to `cache/search_requests/` for later inspection and debugging.

---

# Repo layout (important files)

```
├── src/
│ ├── scrapers/
│ │ └── goodreads.py # Goodreads scraper (rewritten)
│ ├── utils/
│ │ └── database.py # Robust DB layer (rewritten)
│ ├── webui/
│ │ ├── app.py # Flask web UI backend (serves pages)
│ │ └── templates/ # Jinja templates (layout, shelf_view, downloads, status)
│ ├── api/
│ │ └── cwa_client.py # Optional: CWA client wrapper (if present)
│ └── ... # other modules (backend, downloader, models, logger)
├── app/ # optional alternative layout for webui files
├── docker-compose.yml # (your deployment file)
└── README.md # this file
```



---

# Key components & what they do

### `src/utils/database.py`
- Central DB wrapper around SQLite.
- Creates `books` and `history` tables.
- Adds missing columns automatically (so new scraper fields won’t break inserts).
- Exposes:
  - `save_book(book, shelves=None)`
  - `get_book_by_id(goodreads_id)`
  - `update_book(goodreads_id, updates)`
  - `get_books_by_shelf(shelf_name, limit=30, offset=0)`
  - `get_all_books`, `get_shelves`, `add_history`, `get_history`, and utility methods.
- Fixes the prior `binding 39` error by building placeholders dynamically and keeping schema flexible.

### `src/scrapers/goodreads.py`
- Uses the `#ALL#` shelf view with `per_page` param (configurable; default `100`).
- Can run with Selenium (if chromedriver/chrome present) to toggle settings and show all columns, otherwise falls back to `requests`.
- Dynamically maps table headers to canonical keys (title, author, cover, isbn, etc.).
- Extracts series info from title or, if missing, fetches the book page to find series id/number.
- Downloads covers into `cache/covers/` and stores a local filename in DB `cover_local_path`.
- Saves search request JSON files and candidate lists to `cache/search_requests/`.
- Integrates with `CWAClient` or HTTP backend `/api/search` and `/api/download` to search & queue downloads.
- Provides a `run_full_sync_and_queue_downloads(shelf_name="to-download")` helper to fetch the shelf and queue the best candidate for each book.

### `src/webui/app.py`
- Renders shelf pages from the DB (not scraping on demand).
- Default `per_page` for UI = 30 (configurable via `WEBUI_PER_PAGE` env var).
- Computes serial number (`offset + index + 1`) for each row.
- Serves local covers via `/covers/<filename>`.
- Adds `/downloads` UI page that polls `/api/status` to show queue/progress and allows cancel/clear.
- Provides a small local API proxy to enqueue book downloads (`/webui/api/download`) that uses an in-process `backend` module if available or calls `DOWNLOAD_BACKEND_URL`.

### Templates
- `layout.html` — base layout and nav.
- `shelf_view.html` — shelf listing with per-page control and "Download" button for each row.
- `downloads.html` — download queue and controls (Cancel, Clear).
- `status.html` — sync/fetch history partial.

---

# Important directories (in container)

- `/app/data/cache/covers` — downloaded cover images (served by Flask).
- `/app/data/cache/search_requests` — saved search request JSON and candidate dumps.
- `/app/data/databases` — SQLite DB files (goodreads.db or as configured).
- `/app/logs` — application logs.

---

# Config / Environment variables

Common variables you can set (examples):

- `GOODREADS_USER_ID` or put in your config dict: Goodreads numeric user id.
- `GOODREADS_DB_PATH` — path to SQLite DB (default `/app/data/databases/goodreads.db`).
- `WEBUI_PER_PAGE` — default per-page for the Web UI (default `30`).
- `GOODREADS_PER_PAGE` — per_page used by the scraper (default `100`).
- `DOWNLOAD_BACKEND_URL` — fallback HTTP backend URL for search/download endpoints (default `http://localhost:8080`).
- `CWA_API_URL`, `CWA_USERNAME`, `CWA_PASSWORD` — if you use the `CWAClient` to search/queue.
- Selenium:
  - `HEADLESS=1` (default) or `0` to show browser.
  - `CHROMIUM_BIN` — path to chromium binary.
  - `CHROMEDRIVER_PATH` — path to chromedriver.
- Ports / Flask config as usual.

Example `docker-compose` snippet:
```yaml
services:
  ebooks:
    image: your-image
    environment:
      - WEBUI_PER_PAGE=30
      - GOODREADS_PER_PAGE=100
      - DOWNLOAD_BACKEND_URL=http://cwa-backend:8080
      - CHROMEDRIVER_PATH=/usr/bin/chromedriver
      - CHROMIUM_BIN=/usr/bin/chromium
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    ports:
      - "5000:5000"
```

---

# Key components & what they do

### `src/utils/database.py`
- Central DB wrapper around SQLite.
- Creates `books` and `history` tables.
- Adds missing columns automatically (so new scraper fields won’t break inserts).
- Exposes:
  - `save_book(book, shelves=None)`
  - `get_book_by_id(goodreads_id)`
  - `update_book(goodreads_id, updates)`
  - `get_books_by_shelf(shelf_name, limit=30, offset=0)`
  - `get_all_books`, `get_shelves`, `add_history`, `get_history`, and utility methods.
- Fixes the prior `binding 39` error by building placeholders dynamically and keeping schema flexible.

### `src/scrapers/goodreads.py`
- Uses the `#ALL#` shelf view with `per_page` param (configurable; default `100`).
- Can run with Selenium (if chromedriver/chrome present) to toggle settings and show all columns, otherwise falls back to `requests`.
- Dynamically maps table headers to canonical keys (title, author, cover, isbn, etc.).
- Extracts series info from title or, if missing, fetches the book page to find series id/number.
- Downloads covers into `cache/covers/` and stores a local filename in DB `cover_local_path`.
- Saves search request JSON files and candidate lists to `cache/search_requests/`.
- Integrates with `CWAClient` or HTTP backend `/api/search` and `/api/download` to search & queue downloads.
- Provides a `run_full_sync_and_queue_downloads(shelf_name="to-download")` helper to fetch the shelf and queue the best candidate for each book.

### `src/webui/app.py`
- Renders shelf pages from the DB (not scraping on demand).
- Default `per_page` for UI = 30 (configurable via `WEBUI_PER_PAGE` env var).
- Computes serial number (`offset + index + 1`) for each row.
- Serves local covers via `/covers/<filename>`.
- Adds `/downloads` UI page that polls `/api/status` to show queue/progress and allows cancel/clear.
- Provides a small local API proxy to enqueue book downloads (`/webui/api/download`) that uses an in-process `backend` module if available or calls `DOWNLOAD_BACKEND_URL`.

### Templates
- `layout.html` — base layout and nav.
- `shelf_view.html` — shelf listing with per-page control and "Download" button for each row.
- `downloads.html` — download queue and controls (Cancel, Clear).
- `status.html` — sync/fetch history partial.

---

# Important directories (in container)

- `/app/data/cache/covers` — downloaded cover images (served by Flask).
- `/app/data/cache/search_requests` — saved search request JSON and candidate dumps.
- `/app/data/databases` — SQLite DB files (goodreads.db or as configured).
- `/app/logs` — application logs.

---

# Config / Environment variables

Common variables you can set (examples):

- `GOODREADS_USER_ID` or put in your config dict: Goodreads numeric user id.
- `GOODREADS_DB_PATH` — path to SQLite DB (default `/app/data/databases/goodreads.db`).
- `WEBUI_PER_PAGE` — default per-page for the Web UI (default `30`).
- `GOODREADS_PER_PAGE` — per_page used by the scraper (default `100`).
- `DOWNLOAD_BACKEND_URL` — fallback HTTP backend URL for search/download endpoints (default `http://localhost:8080`).
- `CWA_API_URL`, `CWA_USERNAME`, `CWA_PASSWORD` — if you use the `CWAClient` to search/queue.
- Selenium:
  - `HEADLESS=1` (default) or `0` to show browser.
  - `CHROMIUM_BIN` — path to chromium binary.
  - `CHROMEDRIVER_PATH` — path to chromedriver.
- Ports / Flask config as usual.

Example `docker-compose` snippet:
```yaml
services:
  ebooks:
    image: your-image
    environment:
      - WEBUI_PER_PAGE=30
      - GOODREADS_PER_PAGE=100
      - DOWNLOAD_BACKEND_URL=http://cwa-backend:8080
      - CHROMEDRIVER_PATH=/usr/bin/chromedriver
      - CHROMIUM_BIN=/usr/bin/chromium
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    ports:
      - "5000:5000"
```

# Goodreads Scraper & Downloader Web UI

This project provides a web interface and backend scraper to sync your Goodreads shelves, download book covers, and queue books for download through an external backend service.

## How to Run

You can run the application locally for development or deploy it using Docker.

### Local (for development)

1.  Create and activate a Python virtual environment.
2.  Install the required dependencies:
    ```bash
    pip install -r requirements.txt
    ```
    *(Ensure `selenium`, `beautifulsoup4`, `requests`, `flask`, etc., are included).*
3.  Make sure the database path (defined by `GOODREADS_DB_PATH` or the default) is writable.
4.  Start the web application:
    ```bash
    python -m src.webui.app
    ```
    or
    ```bash
    python src/webui/app.py
    ```

### Docker

1.  Build the Docker image from the provided Dockerfile.
2.  Run the container, ensuring volumes for `/app/data` and `/app/logs` are mounted to persist data.
3.  If using Selenium, set the following environment variables in your `docker-compose.yml` or run command:
    * `CHROMEDRIVER_PATH`
    * `CHROMIUM_BIN`

---

## Typical Workflows

### 1. Initial Sync

-   Start the application (or run the scraper script directly).
-   The scraper will automatically fetch your entire Goodreads `#ALL#` shelf, paginating through the results.
-   It downloads book covers locally and saves all book data as rows in the database.
-   You can check the `/covers/` directory to see the saved images.

### 2. Queue Downloads

-   Navigate to a shelf in the web UI (e.g., `/shelf/<shelf_name>`).
-   Click the "Download" button for a book.
-   The backend performs a search using the cleaned title and author's first name.
-   A JSON request is logged in `cache/search_requests/`.
-   The best candidate is chosen based on a scoring algorithm and queued for download via the configured backend API (CWA or a fallback HTTP service).

### 3. Monitor

-   Open the `/downloads` page in the web UI to monitor the queue and see active downloads.

---

## Troubleshooting & Common Errors

-   **`sqlite3.ProgrammingError: You did not supply a value for binding NN`**
    -   **Fix:** This is handled by the rewritten Database module which constructs placeholders dynamically and migrates missing columns. If you still encounter this, ensure your DB file is correct. As a last resort, remove the database file to start fresh.

-   **Missing covers or permission errors writing to the covers directory**
    -   **Fix:** Ensure the `/app/data/cache/covers` directory is writable by the application user inside the container. You may need to adjust permissions with `chown` or `chmod`.

-   **Selenium errors (e.g., Chromedriver missing)**
    -   **Fix:** Ensure the `CHROMEDRIVER_PATH` environment variable matches the path to the installed chromedriver binary and `CHROMIUM_BIN` points to your chrome/chromium executable. The scraper will fall back to using `requests` if Selenium is not available.

-   **Login/cookie issues for private Goodreads data**
    -   **Fix:** The scraper can load and reuse Selenium cookies from `cache/logins/goodreads/cookies.pkl`. To scrape private content, perform an initial login with Selenium locally, save the cookies, and ensure the cookie file is included in your container.

-   **Backend integration errors**
    -   **Fix:** Verify that the `DOWNLOAD_BACKEND_URL` is correct and reachable from the application. The backend must support the `/api/search`, `/api/download`, and `/api/status` endpoints. Alternatively, provide an in-process backend module.

-   **"Page shows 30 books but per_page was set to 100"**
    -   **Fix:** The UI and the scraper have separate `per_page` settings. The default for the UI is 30, while the scraper defaults to 100. Adjust the relevant environment variables to match your expectations.

---

## Developer Notes & Next Steps

Here are some recommended improvements:

-   [ ] Add unit tests for `Database` methods and scraper parsing logic (especially `_map_row_by_headers`).
-   [ ] Improve the search scoring logic and expose the top-N candidates in the UI to allow for manual override.
-   [ ] Add authentication (Basic Auth or token-based) to the Web UI if exposing it to the internet.
-   [ ] Implement retries and rate-limiting for external requests to avoid IP bans.
-   [ ] Integrate a background worker/queue (e.g., Celery, RQ) for managing download jobs to improve concurrency.
-   [ ] Add optional image resizing/caching and serve static assets through a CDN or reverse-proxy.
-   [ ] Create a small admin/settings page in the UI to edit configuration (like `per_page` values and backend URLs) without restarting the application.

---

## File & Directory Reference

### Saved Data Locations

-   **Search Requests:** `cache/search_requests/search_<goodreads_id>_<timestamp>.json`
-   **Search Results/Candidates:** `cache/search_requests/candidates_<goodreads_id>_<timestamp>.json`

*These files are invaluable for debugging why the backend failed to find a good match for a book.*

### Quick References

-   **DB File Location (default):** `/app/data/databases/goodreads.db`
-   **Cover Directory (default):** `/app/data/cache/covers`
-   **Saved Search Requests:** `/app/data/cache/search_requests/`

### Web UI Endpoints

-   **Homepage:** `http://<host>:5000/`
-   **Shelves:** `/shelf/to-download`, `/shelf/to-read`, `/shelf/read`
-   **Downloads Queue:** `/downloads`
-   **Serve Covers:** `/covers/<filename>`

---

## How to Ask for Help

When you need assistance, provide the following information to get a concise and helpful response:

1.  **Short project summary:** You can copy the first paragraph of this README.
2.  **Path to the relevant code:** (e.g., `src/scrapers/goodreads.py`, `src/utils/database.py`, `src/webui/app.py`).
3.  **A sample of recent logs:** The last 50 lines showing the error or behavior you want to debug.
4.  **Exact steps you just ran:** How you started the app, environment variables, and whether Selenium is available.
5.  **The desired behavior:** (e.g., “fix cover download permission”, “improve search scoring”, “make webui list sortable”).

### Example Prompt:

> I run the project with Docker using these envs: `GOODREADS_PER_PAGE=100`, `WEBUI_PER_PAGE=30`, `DOWNLOAD_BACKEND_URL=http://cwa:8080`. The web UI shows 30 per page but scraper logs say it fetched 100 per page. The UI raises `TypeError: get_books_by_shelf() got an unexpected keyword argument 'offset'`. Here are the last 50 log lines: (paste logs here). Please fix any remaining DB function mismatches and make the UI use the DB offset properly.