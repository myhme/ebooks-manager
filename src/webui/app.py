# src/webui/app.py
import os
import logging
from flask import Flask, request, render_template, send_from_directory, jsonify, redirect, url_for
from urllib.parse import urlencode
from pathlib import Path

from src.utils.database import Database

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = Flask(__name__, template_folder="templates", static_folder="static")

# Configuration defaults - override via env if needed
PER_PAGE_DEFAULT = int(os.getenv("WEBUI_PER_PAGE", "30"))
COVER_DIR = os.getenv("COVERS_DIR", "/app/data/cache/covers")
DOWNLOAD_BACKEND_URL = os.getenv("DOWNLOAD_BACKEND_URL", "http://localhost:8080")  # optional

# Ensure cover dir exists
Path(COVER_DIR).mkdir(parents=True, exist_ok=True)

# Create DB instance (uses default path if not configured)
DB_PATH = os.getenv("GOODREADS_DB_PATH", "/app/data/databases/goodreads.db")
db = Database(DB_PATH)


@app.route("/")
def index():
    # redirect to a default shelf page
    return redirect(url_for("shelf_view", shelf_name="to-read"))


@app.route("/shelf/<shelf_name>")
def shelf_view(shelf_name):
    """
    Renders shelf table using DB results (not live scraping).
    Query params:
      - page (1-indexed)
      - per_page
    """
    try:
        page = int(request.args.get("page", "1"))
        per_page = int(request.args.get("per_page", str(PER_PAGE_DEFAULT)))
    except Exception:
        page = 1
        per_page = PER_PAGE_DEFAULT

    offset = (page - 1) * per_page
    books = db.get_books_by_shelf(shelf_name, limit=per_page, offset=offset)

    # compute serial numbers for display
    for idx, b in enumerate(books):
        b["_serial"] = offset + idx + 1
        # choose cover src: local if available, else remote
        if b.get("cover_local_path"):
            b["_cover_src"] = f"/covers/{b['cover_local_path']}"
        elif b.get("cover_url"):
            b["_cover_src"] = b["cover_url"]
        else:
            b["_cover_src"] = "/static/img/cover-placeholder.png"

        # author normalization for UI
        if not b.get("author_first") and b.get("author"):
            # quick fallback: "Lastname, Firstname" -> "Firstname Lastname"
            a = b.get("author")
            if "," in a:
                parts = [p.strip() for p in a.split(",")]
                if len(parts) >= 2:
                    b["author_first"] = " ".join(parts[::-1])
                else:
                    b["author_first"] = a
            else:
                b["author_first"] = a

    # total pages calculation (rough)
    total = db.row_count()
    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template("shelf_view.html",
                           shelf_name=shelf_name,
                           books=books,
                           page=page,
                           per_page=per_page,
                           total_pages=total_pages)


@app.route("/covers/<path:filename>")
def serve_cover(filename):
    """Serve downloaded covers from the shared cache directory."""
    # secure path by disallowing ../
    filename = os.path.basename(filename)
    return send_from_directory(COVER_DIR, filename)


# Downloads page - UI will poll the underlying download backend
@app.route("/downloads")
def downloads_page():
    return render_template("downloads.html")


# Simple API proxy helpers for the webui to talk to the download backend.
# First try to use an in-process backend module if present; otherwise forward via HTTP.

def _call_backend_api(path, params=None):
    """
    Calls backend via HTTP. Caller should handle exceptions.
    """
    import requests
    params = params or {}
    url = DOWNLOAD_BACKEND_URL.rstrip("/") + "/" + path.lstrip("/")
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        LOG.exception("Backend HTTP call failed %s %s", url, e)
        return {"error": str(e)}


@app.route("/webui/api/download", methods=["POST"])
def webui_queue_download():
    """
    Queue a download for a goodreads book id.
    Body: json { "id": "<goodreads_id>", "priority": <int> }
    """
    data = request.get_json() or {}
    gid = data.get("id") or request.form.get("id")
    priority = int(data.get("priority", 0))
    if not gid:
        return jsonify({"error": "id required"}), 400

    # Try in-process backend first (if available)
    try:
        import backend as local_backend  # optional in your project
        ok = local_backend.queue_book(gid, priority)
        if ok:
            return jsonify({"status": "queued", "id": gid})
    except Exception:
        LOG.debug("No local backend or queue_book failed; falling back to HTTP")

    # Fallback to calling external backend API
    resp = _call_backend_api("/api/download", params={"id": gid, "priority": priority})
    return jsonify(resp)


@app.route("/webui/api/status")
def webui_status():
    try:
        import backend as local_backend
        status = local_backend.queue_status()
        return jsonify(status)
    except Exception:
        return jsonify(_call_backend_api("/api/status"))


# Serve static status partial used within pages
@app.route("/status_partial")
def status_partial():
    history = db.get_history(limit=200)
    return render_template("status.html", history=history)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=os.getenv("DEBUG", "0") == "1")
