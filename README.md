# Ebooks Manager

A Python-based project to manage ebook downloads by scraping Goodreads shelves using Selenium and interfacing with the Calibre-Web-Automated-Book-Downloader API.

## Setup

1. Copy `config/config.json.template` to `/mnt/data/docker/docker-config/ebooks-manager/config/config.json` or set environment variables (e.g., `GOODREADS_USERNAME`, `GOODREADS_PASSWORD`, `CWA_API_URL`, etc.) for credentials.
2. Copy `.env.example` to `.env` in the project root and adjust as needed.
3. Run `bash setup_project.sh` to create the project structure.
4. Run `docker-compose up --build` to build and start the containers (ebooks-manager and flaresolverr).
5. Access the web interface at `http://ebooks-manager.mayserver.local:5002`.

## Directory Structure

- `config/`: Configuration files
- `data/`: Databases, cache, and webpage storage
- `logs/`: History, sync logs, and screenshots
- `scripts/`: Entrypoint and setup scripts
- `src/`: Source code
  - `api/`: API clients (e.g., CWA)
  - `scrapers/`: Website scraping logic
  - `utils/`: Shared utilities
  - `webui/`: Web interface

## Environment Variables

- `GOODREADS_USERNAME`, `GOODREADS_PASSWORD`, `GOODREADS_USER_ID`: Goodreads credentials
- `CWA_API_URL`, `CWA_USERNAME`, `CWA_PASSWORD`: Calibre-Web API details
- `LOG_LEVEL`: Set to `DEBUG` for detailed logs and screenshots
- `DRY_RUN`: Set to `yes` to skip actual downloads
- `WEBPAGE_CACHE_DAYS`: Cache expiration in days (default: 30)
- `FLARESOLVERR_URL`: FlareSolverr service URL (default: `http://flaresolverr:8191/v1`)

## Debugging
- All screenshots are now full-page captures.
- If `LOG_LEVEL=DEBUG`, login failures trigger three rapid screenshots for diagnostics.
- Check `/status`, `/history`, `/shelf/to-download`, and `/sync` endpoints in the web UI.
