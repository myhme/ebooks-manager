#!/usr/bin/env python3
# src/webui/app.py
"""
src/webui/app.py
Patched FastAPI WebUI with:
- WebSocket broadcast
- whitelist redaction of config broadcasts
- background queue worker (sync + queue)
- proxy endpoints to CWA
- page endpoints + partial endpoints for AJAX refresh
- admin config endpoints (save -> broadcast safe whitelist)
- robust CWA URL normalization and additional debug logging for search responses
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

try:
    from src.utils.database import Database
except Exception:
    from src.utils.database import Database

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

CONFIG_PATH = Path(os.getenv("APP_CONFIG_JSON", "/app/config/config.json"))
if not CONFIG_PATH.exists():
    alt = Path.cwd() / "config" / "config.json"
    if alt.exists():
        CONFIG_PATH = alt


def load_config() -> Dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        # fallback to environment variables
        return {k.lower(): v for k, v in os.environ.items()}


DB_PATH = os.getenv("DATABASE_PATH") or load_config().get("database_path") or "/app/data/databases/goodreads.db"
_db = Database(DB_PATH)

# single AsyncClient used by the app
http_client = httpx.AsyncClient(timeout=30.0)

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
    return {k: v for k, v in cfg.items() if k in SAFE_CONFIG_KEYS}


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


def _build_cwa_url(base: str, path: str) -> str:
    """
    Build a correct URL to talk to the CWA backend, handling several variants:
      - base might already include '/request/api' (e.g. http://host:8084/request/api)
      - base might include '/request' (e.g. http://host:8084/request)
      - base might include '/api' (e.g. http://host:8084/api) or just host (http://host:8084)
      - path might be 'api/status' or '/api/status' or 'status' or '/request/api/status'
    Goal: produce one well-formed URL which points to the CWA API endpoint, typically under:
      - /request/api/<endpoint>  (preferred default)
    """
    b = base.rstrip("/")
    p = path.lstrip("/")

    # If base already contains /request/api or /api at the end, drop duplicate prefix from path
    if b.endswith("/request/api") or b.endswith("/api"):
        if p.startswith("request/api/"):
            p = p[len("request/api/"):]
        elif p.startswith("api/"):
            p = p[len("api/"):]
        return f"{b}/{p}"

    # If base ends with /request (but not /request/api)
    if b.endswith("/request"):
        # If path begins with request/api or api, avoid duplicating
        if p.startswith("request/api/"):
            p = p[len("request/api/"):]
            return f"{b}/{p}"
        if p.startswith("api/"):
            return f"{b}/{p}"
        return f"{b}/api/{p}"

    # base is host-only (no /request, no /api)
    # If path starts with request/api -> just append
    if p.startswith("request/api/"):
        return f"{b}/{p}"
    # If path starts with api/ -> prefix with request
    if p.startswith("api/"):
        return f"{b}/request/{p}"
    # typical case: add /request/api/
    return f"{b}/request/api/{p}"


async def call_cwa_api(
    path: str,
    method: str = "GET",
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    timeout: float = 10.0,
):
    """
    Call the Calibre-Web-Automated-Book-Downloader backend.

    Normalizes base URL + path so callers can pass 'api/status', '/api/search', 'search', etc.
    Logs detailed info about request/response for easier debugging.
    """
    base, user, pw = _get_cwa_config()
    if not base:
        LOG.error("❌ No CWA backend configured")
        return False, 500, {"error": "No backend configured"}

    url = _build_cwa_url(base, path)
    auth_info = "with-auth" if (user and pw) else "no-auth"

    LOG.info("➡️  CWA API request: %s %s params=%s json=%s %s timeout=%s",
             method, url, params, json_body, auth_info, timeout)

    try:
        r = await http_client.request(
            method,
            url,
            params=params,
            json=json_body,
            auth=(user, pw) if user and pw else None,
            timeout=timeout,
        )

        content_type = r.headers.get("content-type", "")
        status = r.status_code

        # Capture a snippet of the raw response (up to 2k chars) for debugging
        text_snippet = None
        if r.content and len(r.content) > 0:
            try:
                text_snippet = r.content.decode("utf-8", errors="replace")[:2000]
            except Exception:
                text_snippet = str(r.content)[:2000]

        LOG.info("⬅️  CWA API response: %s %s content-type=%s len=%s",
                 status, url, content_type, len(r.content) if r.content else 0)

        if "application/json" in content_type:
            try:
                parsed = r.json()
                if isinstance(parsed, dict):
                    LOG.debug("✅ Parsed JSON (dict) keys=%s", list(parsed.keys()))
                elif isinstance(parsed, list):
                    LOG.debug("✅ Parsed JSON (list) length=%d", len(parsed))
                else:
                    LOG.debug("✅ Parsed JSON type=%s", type(parsed).__name__)
                return True, status, parsed
            except Exception as e:
                LOG.warning("⚠️ Failed to parse JSON from %s: %s", url, e)
                if text_snippet:
                    LOG.debug("Raw CWA response snippet: %s", text_snippet)
                return False, 502, {"error": "invalid_json", "raw": text_snippet}

        # Non-JSON responses (HTML, text, etc.)
        LOG.debug("ℹ️ Non-JSON response snippet: %s", text_snippet)
        return True, status, text_snippet or r.text

    except httpx.RequestError as e:
        LOG.exception("❌ Backend request failed: %s", e)
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
                # queue using the CWA API search endpoint
                await call_cwa_api(
                    "/api/download",
                    method="GET",
                    params={"id": job["id"], "priority": job.get("priority", 0)},
                )
                await broadcast({"event": "queued", "candidate_id": job["id"]})

            elif typ == "sync_shelf":
                cfg = load_config()
                cfg["cache_dir"] = cfg.get("cache_dir", "/app/data/cache")
                if not GoodreadsScraper:
                    LOG.warning("GoodreadsScraper missing - skipping actual sync")
                    await broadcast({"event": "sync_done", "shelf": job["shelf"], "book_count": 0})
                else:
                    gs = GoodreadsScraper(cfg)
                    loop = asyncio.get_running_loop()

                    def run_sync():
                        return gs.get_goodreads_books_from_shelf(job["shelf"], False, 200)

                    result = await loop.run_in_executor(None, run_sync)
                    count = len(result) if isinstance(result, (list, tuple)) else 0
                    await broadcast({"event": "sync_done", "shelf": job["shelf"], "book_count": count})
        except Exception:
            LOG.exception("Worker error")
        finally:
            task_queue.task_done()


@app.on_event("startup")
async def on_startup():
    app.state.worker_task = asyncio.create_task(queue_worker())


@app.on_event("shutdown")
async def on_shutdown():
    # Cancel worker and close client
    try:
        app.state.worker_task.cancel()
    except Exception:
        pass
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
            # keep connection alive; client doesn't need to send anything meaningful
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
    # if credentials not set in env, allow access
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


@app.get("/shelf/{shelf}", response_class=HTMLResponse)
async def shelf_view(request: Request, shelf: str, page: int = 1, per_page: int = 30):
    loop = asyncio.get_running_loop()
    books = await loop.run_in_executor(None, _db.get_books_by_shelf, shelf, per_page, (page - 1) * per_page)
    total = await loop.run_in_executor(None, _db.count_books_by_shelf, shelf)
    total_pages = max(1, (total + per_page - 1) // per_page)
    offset = (page - 1) * per_page
    ctx = {
        "request": request,
        "shelf_name": shelf,
        "books": books,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "offset": offset,
    }
    return TEMPLATES.TemplateResponse("shelf_view.html", ctx)


@app.get("/shelf/{shelf}/partial", response_class=HTMLResponse)
async def shelf_partial(request: Request, shelf: str, page: int = 1, per_page: int = 30):
    loop = asyncio.get_running_loop()
    books = await loop.run_in_executor(None, _db.get_books_by_shelf, shelf, per_page, (page - 1) * per_page)
    total = await loop.run_in_executor(None, _db.count_books_by_shelf, shelf)
    total_pages = max(1, (total + per_page - 1) // per_page)
    offset = (page - 1) * per_page
    ctx = {
        "request": request,
        "shelf_name": shelf,
        "books": books,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "offset": offset,
    }
    return TEMPLATES.TemplateResponse("partials/shelf_table.html", ctx)


@app.get("/downloads", response_class=HTMLResponse)
async def downloads_page(request: Request):
    return TEMPLATES.TemplateResponse("downloads.html", {"request": request})


@app.get("/downloads/partials/queue", response_class=HTMLResponse)
async def downloads_queue_partial(request: Request):
    ok, _, data = await call_cwa_api("api/status")
    # keep shape expected by template
    queue_data = data if ok else {"error": data}
    return TEMPLATES.TemplateResponse("partials/queue_panel.html", {"request": request, "queue_data": queue_data})


@app.get("/downloads/partials/active", response_class=HTMLResponse)
async def downloads_active_partial(request: Request):
    ok, _, data = await call_cwa_api("api/downloads/active")
    active_data = data if ok else {"active_downloads": []}
    return TEMPLATES.TemplateResponse("partials/active_table.html", {"request": request, "active_data": active_data})


@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    return TEMPLATES.TemplateResponse("status.html", {"request": request})


@app.get("/status/partials/queue", response_class=HTMLResponse)
async def status_queue_partial(request: Request):
    ok, _, data = await call_cwa_api("api/status")
    queue_data = data if ok else {"error": data}
    return TEMPLATES.TemplateResponse("partials/queue_panel.html", {"request": request, "queue_data": queue_data})


@app.get("/status/partials/active", response_class=HTMLResponse)
async def status_active_partial(request: Request):
    ok, _, data = await call_cwa_api("api/downloads/active")
    active_data = data if ok else {"active_downloads": []}
    return TEMPLATES.TemplateResponse("partials/active_table.html", {"request": request, "active_data": active_data})


# -------------------------
# Manual Search (page + partial)
# -------------------------
@app.get("/manual_search", response_class=HTMLResponse)
async def manual_search_page(request: Request, q: Optional[str] = None, partial: bool = False):
    """
    Two modes:
      - full page render (no results) - the template's JS will call ?partial=1 to fetch results
      - partial rendering (AJAX) - returns the partial with results inserted
    """
    if partial:
        LOG.info("🔍 Manual search request (partial) q=%s", q)
        results = []
        if q:
            ok, code, data = await call_cwa_api("/api/search", params={"query": q})
            if not ok:
                LOG.warning("❌ CWA search proxy failed for query=%s code=%s", q, code)
                # If backend returned a raw snippet or error, include helpful debug in logs
                if isinstance(data, (str, bytes)):
                    snippet = str(data)[:2000]
                else:
                    snippet = json.dumps(data)[:2000]
                LOG.debug("Raw CWA search response (snippet) for q=%s: %s", q, snippet)
            else:
                # backend may return list or { "books": [...] } or { "results": [...] }
                if isinstance(data, list):
                    results = data
                    LOG.info("✅ CWA search for q=%s returned list with %d items", q, len(results))
                elif isinstance(data, dict):
                    results = data.get("books") or data.get("results") or data.get("items") or []
                    LOG.info("✅ CWA search for q=%s returned dict with keys=%s -> %d results",
                             q, list(data.keys()), len(results))
                else:
                    results = []
                    LOG.warning("⚠️ Unexpected CWA search response type for q=%s: %s",
                                q, type(data).__name__)

            if results:
                try:
                    LOG.debug("First result sample: %s", json.dumps(results[0], indent=2)[:1000])
                except Exception as e:
                    LOG.warning("Failed to log first result sample: %s", e)

            LOG.info("✅ CWA search for q=%s returned %s items", q, len(results))


        # render partial template (partials/search_results.html expected)
        return TEMPLATES.TemplateResponse(
            "partials/search_results.html",
            {"request": request, "results": results},
        )

    # Full page render (empty until JS loads partials)
    LOG.info("🌐 Manual search page load (full, no query yet)")
    return TEMPLATES.TemplateResponse("search.html", {"request": request})


@app.get("/sync", response_class=HTMLResponse)
async def sync_page(request: Request):
    return TEMPLATES.TemplateResponse("sync.html", {"request": request})


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
    ok, code, data = await call_cwa_api("api/status")
    return JSONResponse(status_code=code, content=data if ok else {"error": data})


@app.get("/api/active_proxy")
async def api_active_proxy():
    ok, code, data = await call_cwa_api("api/downloads/active")
    return JSONResponse(status_code=code, content=data if ok else {"error": data})


# -------------------------
# Search + queue APIs
# -------------------------
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
        LOG.error("Backend search failed for %s: %s", query, resp)
        raise HTTPException(502, "Backend search failed")

    candidates = resp if isinstance(resp, list) else resp.get("books", []) if isinstance(resp, dict) else []
    if not candidates:
        # helpful debug if backend returned something unexpected
        LOG.debug("Search returned no candidates for query=%s; raw response: %s", query, resp)
        return {"error": "No candidates"}

    def score(c):
        s = 0
        if isbn and isbn.replace("-", "") in str(c.get("isbn", "")).replace("-", ""):
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
            await task_queue.put({"type": "queue_candidate", "id": cand_id, "priority": 0})
            return {"queued": True, "candidate": best_cand}
    return {"queued": False, "candidates": candidates[:6]}


@app.post("/api/queue_from_candidate")
async def api_queue_from_candidate(payload: dict):
    cand_id = payload.get("candidate_id") or payload.get("id")
    if not cand_id:
        raise HTTPException(400, "candidate_id required")
    await task_queue.put({"type": "queue_candidate", "id": cand_id, "priority": payload.get("priority", 0)})
    return {"queued": True, "id": cand_id}


@app.post("/sync/{shelf}")
async def trigger_sync(shelf: str):
    await task_queue.put({"type": "sync_shelf", "shelf": shelf})
    return {"status": "sync_started", "shelf": shelf}


# -------------------------
# Admin config endpoints
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
        await broadcast({"event": "config_updated", "config": safe_cfg, "timestamp": int(time.time())})
        return JSONResponse(status_code=200, content={"ok": True, "config": payload})
    except Exception as e:
        LOG.exception("Failed to write config")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
