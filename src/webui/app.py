import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi import (
    FastAPI,
    Request,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    Depends,
    Form,
)
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    FileResponse,
    JSONResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from src.utils.database import Database
from src.scrapers.goodreads import GoodreadsScraper

# --- Logging ---
LOG = logging.getLogger("webui")
logging.basicConfig(level=logging.INFO)

# --- FastAPI app ---
app = FastAPI(
    title="Ebooks Manager WebUI",
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ],
)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# --- Config loader ---
CONFIG_PATH = Path(os.getenv("APP_CONFIG_JSON", "/app/config/config.json"))
if not CONFIG_PATH.exists():
    alt = Path.cwd() / "config" / "config.json"
    if alt.exists():
        CONFIG_PATH = alt


def load_config() -> Dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {k.lower(): v for k, v in os.environ.items()}


# --- Database ---
DB_PATH = (
    os.getenv("DATABASE_PATH")
    or os.getenv("database_path")
    or load_config().get("database_path")
    or "/app/data/databases/goodreads.db"
)
_db = Database(DB_PATH)

# --- Async http client ---
http_client = httpx.AsyncClient(timeout=30.0)

# --- Background task queue ---
task_queue: "asyncio.Queue[dict]" = asyncio.Queue()
ws_connections: set[WebSocket] = set()


async def queue_worker():
    LOG.info("Queue worker started")
    while True:
        job = await task_queue.get()
        try:
            if job.get("type") == "queue_candidate":
                await call_cwa_api(
                    "/api/download",
                    "GET",  # downloader expects GET with ?id
                    params={"id": job["id"], "priority": job.get("priority", 0)},
                )
                await broadcast({"event": "queued", "candidate_id": job["id"]})
            elif job.get("type") == "sync_shelf":
                cfg = load_config()
                cfg["cache_dir"] = cfg.get("cache_dir", "/app/data/cache")
                gs = GoodreadsScraper(cfg)
                loop = asyncio.get_running_loop()

                # run sync in thread, return list of books
                def run_sync():
                    return gs.get_goodreads_books_from_shelf(job["shelf"], False, 200)

                result = await loop.run_in_executor(None, run_sync)
                synced_count = len(result) if isinstance(result, (list, tuple)) else 0

                await broadcast({
                    "event": "sync_done",
                    "shelf": job["shelf"],
                    "count": synced_count,
                })
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


# --- Helpers ---
def _get_cwa_config():
    cfg = load_config()
    base = cfg.get("cwa_api_url") or os.getenv("CWA_API_URL")
    user = cfg.get("cwa_username") or os.getenv("CWA_USERNAME")
    pw = cfg.get("cwa_password") or os.getenv("CWA_PASSWORD")

    if not base:
        LOG.error("❌ No cwa_api_url configured. Backend calls will fail.")
        return None, user, pw

    LOG.info("✅ Using CWA backend at %s", base)
    return base.rstrip("/"), user, pw


async def call_cwa_api(
    path: str,
    method: str = "GET",
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    timeout: float = 10.0,
):
    base, user, pw = _get_cwa_config()
    if not base:
        return False, 500, "No backend configured"

    url = f"{base}/{path.lstrip('/')}"
    LOG.debug("➡️ Calling backend %s %s", method, url)

    try:
        r = await http_client.request(
            method,
            url,
            params=params,
            json=json_body,
            auth=(user, pw) if user and pw else None,
            timeout=timeout,
        )
        LOG.debug("⬅️ Response %s %s", r.status_code, r.headers.get("content-type"))

        if "application/json" in r.headers.get("content-type", ""):
            return True, r.status_code, r.json()
        return True, r.status_code, r.text
    except httpx.RequestError as e:
        LOG.error("❌ Backend call failed: %s", e)
        return False, 502, str(e)


# --- Admin Auth ---
security = HTTPBasic()
ADMIN_USER = os.getenv("ADMIN_USER")
ADMIN_PASS = os.getenv("ADMIN_PASS")


def check_basic_auth(creds: HTTPBasicCredentials = Depends(security)):
    if not ADMIN_USER or not ADMIN_PASS:
        return True
    if creds.username == ADMIN_USER and creds.password == ADMIN_PASS:
        return True
    raise HTTPException(401, "Unauthorized")


# --- Routes ---
@app.get("/", response_class=HTMLResponse)
async def index():
    return RedirectResponse("/shelf/to-download")


@app.get("/shelf/{shelf}", response_class=HTMLResponse)
async def shelf_view(request: Request, shelf: str, page: int = 1, per_page: int = 30):
    loop = asyncio.get_running_loop()
    books = await loop.run_in_executor(
        None, _db.get_books_by_shelf, shelf, per_page, (page - 1) * per_page
    )
    total = await loop.run_in_executor(None, _db.count_books_by_shelf, shelf)
    total_pages = max(1, (total + per_page - 1) // per_page)
    offset = (page - 1) * per_page
    return TEMPLATES.TemplateResponse(
        "shelf_view.html",
        {
            "request": request,
            "shelf_name": shelf,
            "books": books,
            "page": page,
            "total_pages": total_pages,
            "offset": offset,
        },
    )


@app.get("/covers/{path:path}")
async def serve_cover(path: str, auth: bool = Depends(check_basic_auth)):
    covers_dir = Path(load_config().get("cache_dir", "/app/data/cache")) / "covers"
    f = covers_dir / path
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(str(f))


@app.get("/downloads", response_class=HTMLResponse)
async def downloads_page(request: Request):
    return TEMPLATES.TemplateResponse("downloads.html", {"request": request})


@app.get("/manual_search", response_class=HTMLResponse)
async def manual_search_page(request: Request):
    return TEMPLATES.TemplateResponse("search.html", {"request": request})


@app.get("/sync", response_class=HTMLResponse)
async def sync_page(request: Request):
    return TEMPLATES.TemplateResponse("sync.html", {"request": request})


@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    return TEMPLATES.TemplateResponse("status.html", {"request": request})


# --- Proxy API Endpoints ---
@app.get("/api/search_proxy")
async def api_search_proxy(query: str):
    ok, code, data = await call_cwa_api("api/search", params={"query": query})
    return JSONResponse(status_code=code, content=data if ok else {"error": data})


@app.post("/api/download_proxy")
async def api_download_proxy(book_id: str = Form(...)):
    ok, code, data = await call_cwa_api(
        "api/download", method="GET", params={"id": book_id}
    )
    return JSONResponse(status_code=code, content=data if ok else {"error": data})


@app.get("/api/status_proxy")
async def api_status_proxy():
    ok, code, data = await call_cwa_api("api/status")
    return JSONResponse(status_code=code, content=data if ok else {"error": data})


@app.get("/api/active_proxy")
async def api_active_proxy():
    ok, code, data = await call_cwa_api("api/downloads/active")
    return JSONResponse(status_code=code, content=data if ok else {"error": data})


# --- Search + Queue APIs ---
@app.post("/api/search_and_queue")
async def api_search_and_queue(payload: dict):
    title = payload.get("title", "")
    author = payload.get("author", "")
    isbn = payload.get("isbn", payload.get("isbn13", ""))
    query = isbn if isbn else f"{title} {author}".strip()

    params = {"query": query}
    if title:
        params["title"] = title
    if author:
        params["author"] = author
    if isbn:
        params["isbn"] = isbn

    ok, _, resp = await call_cwa_api("/api/search", "GET", params=params)
    if not ok:
        raise HTTPException(502, "Backend search failed")

    candidates = resp if isinstance(resp, list) else resp.get("books", [])
    if not candidates:
        return {"error": "No candidates"}

    def score(c: dict) -> int:
        s = 0
        if isbn and isbn.replace("-", "") in str(c.get("isbn", "")).replace("-", ""):
            s += 2000
        if title and title.lower() == (c.get("title") or "").lower():
            s += 1000
        if author and author.lower() in (c.get("author") or "").lower():
            s += 400
        return s

    best_score, best_cand = max(
        ((score(c), c) for c in candidates), key=lambda t: t[0]
    )
    if best_score >= int(load_config().get("auto_queue_score_threshold", 600)):
        cand_id = best_cand.get("id") or best_cand.get("md5")
        if cand_id:
            await task_queue.put(
                {"type": "queue_candidate", "id": cand_id, "priority": 0}
            )
            return {"queued": True, "candidate": best_cand}
    return {"queued": False, "candidates": candidates[:6]}


@app.post("/api/queue_from_candidate")
async def api_queue_from_candidate(payload: dict):
    cand_id = payload.get("candidate_id") or payload.get("id")
    if not cand_id:
        raise HTTPException(400, "candidate_id required")
    await task_queue.put(
        {"type": "queue_candidate", "id": cand_id, "priority": payload.get("priority", 0)}
    )
    return {"queued": True, "id": cand_id}


@app.post("/sync/{shelf}")
async def trigger_sync(shelf: str):
    await task_queue.put({"type": "sync_shelf", "shelf": shelf})
    return {"status": "sync_started", "shelf": shelf}


# --- WebSocket updates ---
@app.websocket("/ws/updates")
async def ws_updates(ws: WebSocket):
    await ws.accept()
    ws_connections.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_connections.discard(ws)


async def broadcast(message: dict):
    dead = []
    for ws in ws_connections:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_connections.discard(ws)


# --- Config admin ---
@app.get("/admin/config")
async def get_config(auth: bool = Depends(check_basic_auth)):
    return load_config()


@app.post("/admin/config")
async def write_config(payload: Dict[str, Any], auth: bool = Depends(check_basic_auth)):
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(CONFIG_PATH)
    return {"ok": True}
