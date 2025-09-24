"""
src/webui/app.py
Patched FastAPI WebUI with:
- WebSocket broadcast
- whitelist redaction of config broadcasts
- background queue worker (sync + queue)
- proxy endpoints to CWA
- page endpoints + partial endpoints for AJAX refresh
- admin config endpoints (save -> broadcast safe whitelist)
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

# local DB interface (assumes your project provides a richer implementation)
try:
    from src.utils.database import Database
except Exception:
    # lightweight fallback to keep webui working (non-production)
    from src.utils.database import Database  # will raise if missing
try:
    from src.scrapers.goodreads import GoodreadsScraper
except Exception:
    GoodreadsScraper = None

LOG = logging.getLogger("webui")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Ebooks Manager WebUI",
    middleware=[Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])],
)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# config path (container default); override with APP_CONFIG_JSON env if needed
CONFIG_PATH = Path(os.getenv("APP_CONFIG_JSON", "/app/config/config.json"))
if not CONFIG_PATH.exists():
    alt = Path.cwd() / "config" / "config.json"
    if alt.exists():
        CONFIG_PATH = alt

def load_config() -> Dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        # fallback to environment variables (lowercased)
        return {k.lower(): v for k, v in os.environ.items()}

# Database path & DB object
DB_PATH = os.getenv("DATABASE_PATH") or load_config().get("database_path") or "/app/data/databases/goodreads.db"
_db = Database(DB_PATH)

# Async HTTP client for backend proxy calls (CWA)
http_client = httpx.AsyncClient(timeout=30.0)

# task queue and websocket set
task_queue: "asyncio.Queue[dict]" = asyncio.Queue()
ws_connections: set[WebSocket] = set()

# -------------------------
# Whitelist redaction for config broadcasts
# -------------------------
SAFE_CONFIG_KEYS = {
    "cwa_api_url",
    "database_path",
    "cache_dir",
    "log_file",
    "history_file",
    "auto_queue_score_threshold",
    "goodreads_user_id",
    "goodreads_per_page",
    "app_env",
    "build_version",
    "release_version",
}

def whitelist_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    safe = {}
    for k, v in cfg.items():
        if k in SAFE_CONFIG_KEYS:
            safe[k] = v
    return safe

# -------------------------
# helper to get CWA backend info
# -------------------------
def _get_cwa_config():
    cfg = load_config()
    base = cfg.get("cwa_api_url") or os.getenv("CWA_API_URL")
    user = cfg.get("cwa_username") or os.getenv("CWA_USERNAME")
    pw = cfg.get("cwa_password") or os.getenv("CWA_PASSWORD")
    if not base:
        LOG.error("No cwa_api_url configured - backend calls will fail")
        return None, user, pw
    return base.rstrip("/"), user, pw

async def call_cwa_api(path: str, method: str="GET", params: Optional[dict]=None, json_body: Optional[dict]=None, timeout: float=10.0):
    base, user, pw = _get_cwa_config()
    if not base:
        return False, 500, {"error": "No backend configured"}
    url = f"{base}/{path.lstrip('/')}"
    try:
        r = await http_client.request(method, url, params=params, json=json_body, auth=(user, pw) if user and pw else None, timeout=timeout)
        content_type = r.headers.get("content-type","")
        if "application/json" in content_type:
            return True, r.status_code, r.json()
        return True, r.status_code, r.text
    except httpx.RequestError as e:
        LOG.exception("Backend request failed: %s", e)
        return False, 502, {"error": str(e)}

# -------------------------
# background queue worker
# -------------------------
async def queue_worker():
    LOG.info("Queue worker started")
    while True:
        job = await task_queue.get()
        try:
            typ = job.get("type")
            if typ == "queue_candidate":
                # CWA downloader expects GET /api/download?id=...
                await call_cwa_api("/api/download", method="GET", params={"id": job["id"], "priority": job.get("priority",0)})
                await broadcast({"event":"queued", "candidate_id": job["id"]})
            elif typ == "sync_shelf":
                cfg = load_config()
                cfg["cache_dir"] = cfg.get("cache_dir", "/app/data/cache")
                if not GoodreadsScraper:
                    LOG.warning("GoodreadsScraper missing - skipping actual sync")
                    await broadcast({"event":"sync_done", "shelf": job["shelf"], "book_count": 0})
                else:
                    gs = GoodreadsScraper(cfg)
                    loop = asyncio.get_running_loop()
                    def run_sync():
                        return gs.get_goodreads_books_from_shelf(job["shelf"], False, 200)
                    result = await loop.run_in_executor(None, run_sync)
                    count = len(result) if isinstance(result, (list,tuple)) else 0
                    await broadcast({"event":"sync_done", "shelf": job["shelf"], "book_count": count})
        except Exception:
            LOG.exception("Worker error")
        finally:
            task_queue.task_done()

@app.on_event("startup")
async def on_startup():
    app.state.worker_task = asyncio.create_task(queue_worker())

@app.on_event("shutdown")
async def on_shutdown():
    app.state.worker_task.cancel()
    await http_client.aclose()

# -------------------------
# WebSocket + broadcast
# -------------------------
@app.websocket("/ws/updates")
async def ws_updates(ws: WebSocket):
    await ws.accept()
    ws_connections.add(ws)
    try:
        while True:
            # keep connection alive; we don't require per-ws messages right now
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_connections.discard(ws)

async def broadcast(message: dict):
    dead = []
    for ws in set(ws_connections):
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for d in dead:
        ws_connections.discard(d)

# -------------------------
# admin auth (basic)
# -------------------------
security = HTTPBasic()
ADMIN_USER = os.getenv("ADMIN_USER")
ADMIN_PASS = os.getenv("ADMIN_PASS")

def check_basic_auth(creds: HTTPBasicCredentials = Depends(security)):
    if not ADMIN_USER or not ADMIN_PASS:
        return True
    if creds.username == ADMIN_USER and creds.password == ADMIN_PASS:
        return True
    raise HTTPException(401, "Unauthorized")

# -------------------------
# Page endpoints + partials
# -------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return RedirectResponse("/shelf/to-download")

# Shelf full page
@app.get("/shelf/{shelf}", response_class=HTMLResponse)
async def shelf_view(request: Request, shelf: str, page: int = 1, per_page: int = 30):
    loop = asyncio.get_running_loop()
    books = await loop.run_in_executor(None, _db.get_books_by_shelf, shelf, per_page, (page-1)*per_page)
    total = await loop.run_in_executor(None, _db.count_books_by_shelf, shelf)
    total_pages = max(1, (total + per_page - 1) // per_page)
    offset = (page - 1) * per_page
    ctx = {"request": request, "shelf_name": shelf, "books": books, "page": page, "per_page": per_page, "total_pages": total_pages, "offset": offset}
    return TEMPLATES.TemplateResponse("shelf_view.html", ctx)

# Shelf partial -> only tbody (used by AJAX)
@app.get("/shelf/{shelf}/partial", response_class=HTMLResponse)
async def shelf_partial(request: Request, shelf: str, page: int = 1, per_page: int = 30):
    loop = asyncio.get_running_loop()
    books = await loop.run_in_executor(None, _db.get_books_by_shelf, shelf, per_page, (page-1)*per_page)
    total = await loop.run_in_executor(None, _db.count_books_by_shelf, shelf)
    total_pages = max(1, (total + per_page - 1) // per_page)
    offset = (page - 1) * per_page
    ctx = {"request": request, "shelf_name": shelf, "books": books, "page": page, "per_page": per_page, "total_pages": total_pages, "offset": offset}
    return TEMPLATES.TemplateResponse("partials/shelf_table.html", ctx)

# downloads page and partials
@app.get("/downloads", response_class=HTMLResponse)
async def downloads_page(request: Request):
    # full page (table is initially empty; frontend will fetch partials)
    return TEMPLATES.TemplateResponse("downloads.html", {"request": request})

@app.get("/downloads/partials/active", response_class=HTMLResponse)
async def downloads_active_partial(request: Request):
    # Collect active downloads from DB or backend if you have it
    active = _db.get_active_downloads() if hasattr(_db, "get_active_downloads") else []
    ctx = {"request": request, "active_downloads": active}
    return TEMPLATES.TemplateResponse("partials/active_table.html", ctx)

@app.get("/downloads/partials/queue", response_class=HTMLResponse)
async def downloads_queue_partial(request: Request):
    # queue status
    q = _db.queue_status() if hasattr(_db, "queue_status") else []
    ctx = {"request": request, "queue_data": q}
    return TEMPLATES.TemplateResponse("partials/queue_panel.html", ctx)

# status page and partials (reuse partials for queue/active)
@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    return TEMPLATES.TemplateResponse("status.html", {"request": request})

@app.get("/status/partials/queue", response_class=HTMLResponse)
async def status_queue_partial(request: Request):
    q = _db.queue_status() if hasattr(_db, "queue_status") else []
    ctx = {"request": request, "queue_data": q}
    return TEMPLATES.TemplateResponse("partials/queue_panel.html", ctx)

# manual search page
@app.get("/manual_search", response_class=HTMLResponse)
async def manual_search_page(request: Request):
    return TEMPLATES.TemplateResponse("search.html", {"request": request})

# sync page
@app.get("/sync", response_class=HTMLResponse)
async def sync_page(request: Request):
    return TEMPLATES.TemplateResponse("sync.html", {"request": request})

# serve covers (protected)
@app.get("/covers/{path:path}")
async def serve_cover(path: str, auth: bool = Depends(check_basic_auth)):
    covers_dir = Path(load_config().get("cache_dir", "/app/data/cache")) / "covers"
    f = covers_dir / path
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(str(f))

# -------------------------
# Proxy endpoints to CWA
# -------------------------
@app.get("/api/search_proxy")
async def api_search_proxy(query: str):
    ok, code, data = await call_cwa_api("/api/search", params={"query": query})
    return JSONResponse(status_code=code, content=data if ok else {"error": data})

@app.post("/api/download_proxy")
async def api_download_proxy(book_id: str = Form(...)):
    ok, code, data = await call_cwa_api("/api/download", method="GET", params={"id": book_id})
    return JSONResponse(status_code=code, content=data if ok else {"error": data})

@app.get("/api/status_proxy")
async def api_status_proxy():
    ok, code, data = await call_cwa_api("/api/status")
    return JSONResponse(status_code=code, content=data if ok else {"error": data})

@app.get("/api/active_proxy")
async def api_active_proxy():
    ok, code, data = await call_cwa_api("/api/downloads/active")
    return JSONResponse(status_code=code, content=data if ok else {"error": data})

# -------------------------
# Search + queue APIs (local)
# -------------------------
@app.post("/api/search_and_queue")
async def api_search_and_queue(payload: dict):
    title = payload.get("title", "")
    author = payload.get("author", "")
    isbn = payload.get("isbn", payload.get("isbn13", ""))
    query = isbn if isbn else f"{title} {author}".strip()
    params = {"query": query}
    if title: params["title"] = title
    if author: params["author"] = author
    if isbn: params["isbn"] = isbn

    ok, _, resp = await call_cwa_api("/api/search", "GET", params=params)
    if not ok:
        raise HTTPException(502, "Backend search failed")
    candidates = resp if isinstance(resp, list) else resp.get("books", [])
    if not candidates:
        return {"error":"No candidates"}

    def score(c):
        s = 0
        if isbn and isbn.replace("-","") in str(c.get("isbn","")).replace("-",""):
            s += 2000
        if title and title.lower() == (c.get("title") or "").lower():
            s += 1000
        if author and author.lower() in (c.get("author") or "").lower():
            s += 400
        return s

    best_score, best_cand = max(((score(c), c) for c in candidates), key=lambda t: t[0])
    if best_score >= int(load_config().get("auto_queue_score_threshold", 600)):
        cand_id = best_cand.get("id") or best_cand.get("md5")
        if cand_id:
            await task_queue.put({"type":"queue_candidate", "id": cand_id, "priority": 0})
            return {"queued": True, "candidate": best_cand}
    return {"queued": False, "candidates": candidates[:6]}

@app.post("/api/queue_from_candidate")
async def api_queue_from_candidate(payload: dict):
    cand_id = payload.get("candidate_id") or payload.get("id")
    if not cand_id:
        raise HTTPException(400, "candidate_id required")
    await task_queue.put({"type":"queue_candidate", "id": cand_id, "priority": payload.get("priority",0)})
    return {"queued": True, "id": cand_id}

@app.post("/sync/{shelf}")
async def trigger_sync(shelf: str):
    await task_queue.put({"type":"sync_shelf", "shelf": shelf})
    return {"status":"sync_started", "shelf": shelf}

# -------------------------
# Admin config endpoints (save and broadcast whitelist)
# -------------------------
@app.get("/admin/config", response_class=JSONResponse)
async def get_config(auth: bool = Depends(check_basic_auth)):
    return JSONResponse(content=load_config())

@app.post("/admin/config")
async def write_config(payload: Dict[str, Any], auth: bool = Depends(check_basic_auth)):
    try:
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(CONFIG_PATH)
        safe_cfg = whitelist_config(payload)
        await broadcast({"event":"config_updated", "config": safe_cfg, "timestamp": int(time.time())})
        # return full config to the saving admin (HTTP response)
        return JSONResponse(status_code=200, content={"ok": True, "config": payload})
    except Exception as e:
        LOG.exception("Failed to write config")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
