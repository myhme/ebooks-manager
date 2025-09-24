# 📚 Ebooks Manager

[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)

A self-hosted web application that seamlessly integrates with your Goodreads account to manage your ebook collection. Scrape your Goodreads shelves, automatically download book covers, and queue books for download through integrated backend services.

## ✨ Features

- 🔄 **Goodreads Integration** - Automatically sync your Goodreads shelves and book metadata
- 🖼️ **Cover Management** - Download and serve book covers locally  
- 🌐 **Web Interface** - Modern web UI to browse shelves and manage downloads
- 📥 **Download Queue** - Queue books for download via Calibre-Web-Automated (CWA) or custom backends
- 🔍 **Smart Search** - Intelligent book matching and scoring algorithms
- 📱 **Responsive Design** - Works on desktop and mobile devices
- 🐳 **Docker Ready** - Easy deployment with Docker Compose
- 🔧 **Configurable** - Extensive configuration options via environment variables

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose
- Goodreads account
- (Optional) Calibre-Web-Automated for book downloads

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/myhme/ebooks-manager.git
   cd ebooks-manager
   ```

2. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

3. **Set up configuration**
   ```bash
   cp config/config.json.template config/config.json
   # Edit config.json with your Goodreads credentials
   ```

4. **Start the application**
   ```bash
   docker-compose up -d
   ```

5. **Access the web interface**
   - Open http://localhost:5002 in your browser
   - Your Goodreads shelves will be automatically synced

## 📋 Configuration

### Environment Variables

Create a `.env` file based on `.env.example`:

| Variable | Default | Description |
|----------|---------|-------------|
| `PUID` | `1000` | User ID for file permissions |
| `PGID` | `1000` | Group ID for file permissions |
| `TZ` | `Asia/Kolkata` | Timezone |
| `LOG_LEVEL` | `DEBUG` | Logging level |
| `FLASK_HOST` | `0.0.0.0` | Web server host |
| `FLASK_PORT` | `5002` | Web server port |
| `DRY_RUN` | `yes` | Enable dry run mode |
| `WEBPAGE_CACHE_DAYS` | `30` | Cache duration for web pages |

### Application Configuration

Edit `config/config.json`:

```json
{
    "goodreads_username": "your_goodreads_email",
    "goodreads_password": "your_goodreads_password", 
    "goodreads_user_id": "12345678",
    "cwa_api_url": "http://cwa-downloader:5000/request/api",
    "cwa_username": "your_cwa_username",
    "cwa_password": "your_cwa_password",
    "database_path": "/app/data/databases/goodreads.db",
    "goodreads_per_page": 100
}
```

### Additional Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GOODREADS_USER_ID` | - | Goodreads numeric user ID |
| `GOODREADS_DB_PATH` | `/app/data/databases/goodreads.db` | SQLite database path |
| `WEBUI_PER_PAGE` | `30` | Books per page in web UI |
| `GOODREADS_PER_PAGE` | `100` | Books per page for scraper |
| `DOWNLOAD_BACKEND_URL` | `http://localhost:8080` | Backend API URL |
| `CWA_API_URL` | - | Calibre-Web-Automated API URL |
| `CWA_USERNAME` | - | CWA username |
| `CWA_PASSWORD` | - | CWA password |
| `HEADLESS` | `1` | Run browser in headless mode |
| `CHROMIUM_BIN` | `/usr/bin/chromium` | Chromium binary path |
| `CHROMEDRIVER_PATH` | `/usr/bin/chromedriver` | ChromeDriver path |

## 🏗️ Architecture

### Project Structure

```
ebooks-manager/
├── src/
│   ├── scrapers/
│   │   └── goodreads.py          # Goodreads scraper with Selenium support
│   ├── utils/
│   │   └── database.py           # SQLite database wrapper
│   ├── webui/
│   │   ├── app.py               # FastAPI web application
│   │   ├── templates/           # Jinja2 templates
│   │   │   ├── layout.html
│   │   │   ├── shelf_view.html
│   │   │   ├── downloads.html
│   │   │   └── ...
│   │   └── static/             # CSS, JS, and static assets
│   ├── api/
│   │   └── cwa_client.py       # CWA integration client
│   └── ...
├── config/
│   └── config.json.template    # Configuration template
├── docker-compose.yml          # Docker Compose configuration
├── Dockerfile                  # Container build instructions
└── README.md                   # This file
```

### Key Components

#### Database Layer (`src/utils/database.py`)
- SQLite-based storage for books and sync history
- Automatic schema migration and column addition
- Pagination support with offset/limit
- Methods: `save_book()`, `get_books_by_shelf()`, `get_shelves()`, etc.

#### Goodreads Scraper (`src/scrapers/goodreads.py`)
- Uses `#ALL#` shelf view for comprehensive book data
- Selenium WebDriver support with requests fallback
- Dynamic column mapping and series extraction
- Cover image downloading and local storage
- Search request logging for debugging

#### Web Interface (`src/webui/app.py`)
- FastAPI-based web application
- Database-driven shelf browsing (no live scraping)
- Local cover image serving
- Download queue management UI
- Real-time status updates

## 🔄 Usage Workflows

### 1. Initial Sync
- Start the application
- Scraper fetches your complete Goodreads library
- Book covers are downloaded locally
- All metadata is stored in SQLite database

### 2. Browse Your Library
- Navigate to http://localhost:5002
- Browse books by shelf (to-read, read, currently-reading, etc.)
- View book details including covers, ratings, and metadata

### 3. Queue Downloads
- Click "Download" button for any book
- System searches for the book using title/author
- Best match is automatically queued via CWA backend
- Monitor progress in the Downloads page

### 4. Monitor Downloads
- Visit `/downloads` page for real-time status
- Cancel or clear downloads as needed
- View download history and logs

## 🐳 Docker Deployment

### Using Docker Compose (Recommended)

```yaml
version: "3.9"
services:
  ebooks-webui:
    build: .
    container_name: ebooks-webui
    ports:
      - "5002:5002"
    environment:
      - GOODREADS_USER_ID=12345678
      - WEBUI_PER_PAGE=30
      - GOODREADS_PER_PAGE=100
      - CWA_API_URL=http://cwa-downloader:5000/request/api
    volumes:
      - ./data:/app/data
      - ./config:/app/config
      - ./logs:/app/logs
    depends_on:
      - cwa-downloader
      
  cwa-downloader:
    image: ghcr.io/calibrain/calibre-web-automated-book-downloader:latest
    container_name: cwa-downloader
    ports:
      - "5000:5000"
    volumes:
      - ./books:/books
```

### Manual Docker Build

```bash
# Build the image
docker build -t ebooks-manager .

# Run the container
docker run -d \
  --name ebooks-manager \
  -p 5002:5002 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/logs:/app/logs \
  -e GOODREADS_USER_ID=12345678 \
  ebooks-manager
```

## 🛠️ Development

### Local Development Setup

1. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure application**
   ```bash
   cp config/config.json.template config/config.json
   # Edit config.json with your settings
   ```

4. **Run the application**
   ```bash
   python -m src.webui.app
   # or
   python src/webui/app.py
   ```

### Available Make Commands

```bash
make rebuild    # Rebuild Docker container
make debug      # Rebuild with debug mode
make up         # Start without rebuild
make down       # Stop containers
make logs       # View container logs
```

## 📁 Data Directories

| Directory | Purpose |
|-----------|---------|
| `/app/data/cache/covers` | Downloaded book cover images |
| `/app/data/cache/search_requests` | Search request logs and candidate matches |
| `/app/data/databases` | SQLite database files |
| `/app/logs` | Application logs |

## 🔧 Troubleshooting

### Common Issues

#### Database Binding Errors
```
sqlite3.ProgrammingError: You did not supply a value for binding NN
```
**Solution:** The database module handles this automatically. If issues persist, delete the database file to start fresh.

#### Cover Download Permission Errors
**Solution:** Ensure the covers directory is writable:
```bash
chmod 755 /app/data/cache/covers
chown appuser:appuser /app/data/cache/covers
```

#### Selenium WebDriver Issues
**Solution:** Verify environment variables:
- `CHROMEDRIVER_PATH` points to valid chromedriver
- `CHROMIUM_BIN` points to valid chromium executable  
- System will fallback to requests if Selenium unavailable

#### Backend Integration Errors
**Solution:** 
- Verify `DOWNLOAD_BACKEND_URL` is accessible
- Ensure backend supports required API endpoints:
  - `/api/search`
  - `/api/download` 
  - `/api/status`

#### Private Goodreads Content
**Solution:** For private shelves, perform initial login with Selenium and save cookies to `cache/logins/goodreads/cookies.pkl`

### Getting Help

When seeking assistance, please provide:

1. **Project summary** (copy from above)
2. **Relevant code paths** (e.g., `src/scrapers/goodreads.py`)
3. **Recent logs** (last 50 lines showing the error)
4. **Exact steps taken** (environment variables, startup method)
5. **Desired behavior** (what you're trying to achieve)

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development Guidelines

- Follow existing code style and patterns
- Add tests for new functionality
- Update documentation for any API changes
- Ensure Docker builds succeed
- Test both Selenium and requests fallback modes

## 🗺️ Roadmap

- [ ] **Testing** - Add comprehensive unit tests
- [ ] **Authentication** - Basic Auth for web interface
- [ ] **Search Improvements** - Better matching algorithms and manual override
- [ ] **Background Jobs** - Celery/RQ integration for downloads
- [ ] **Image Optimization** - Cover resizing and CDN support
- [ ] **Admin Interface** - Web-based configuration management
- [ ] **API Documentation** - OpenAPI/Swagger documentation
- [ ] **Mobile App** - React Native companion app

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [Goodreads](https://goodreads.com) for the book data
- [Calibre-Web-Automated](https://github.com/calibrain/calibre-web-automated-book-downloader) for download integration
- FastAPI and SQLite communities for excellent tools

---

**Happy Reading! 📖**