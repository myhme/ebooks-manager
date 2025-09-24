# src/utils/database.py
"""
Robust SQLite database wrapper for ebooks-manager.

- Maintains `books`, `history`, and `downloads` tables.
- Provides CRUD + pagination + helper functions used by webui and scrapers.
"""

import sqlite3
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any

LOG = logging.getLogger(__name__)

BOOK_COLUMNS = [
    "goodreads_id", "title", "title_clean", "author", "author_first",
    "series_name", "series_number", "series_id",
    "pub_date", "pub_date_edition",
    "num_pages", "isbn", "isbn13", "asin", "language",
    "genres", "json_details", "position",
    "cover_url", "cover_local_path", "book_url",
    "avg_rating", "num_ratings", "rating",
    "shelves", "review", "notes", "comments", "votes",
    "date_read", "date_started", "date_added",
    "date_purchased", "purchase_location",
    "owned", "condition", "format",
    "recommender", "read_count",
    "cover_downloaded", "last_synced",
    "fetched_at"
]

COLUMN_DEFS = {
    "goodreads_id": "TEXT PRIMARY KEY",
    "title": "TEXT",
    "title_clean": "TEXT",
    "author": "TEXT",
    "author_first": "TEXT",
    "series_name": "TEXT",
    "series_number": "TEXT",
    "series_id": "TEXT",
    "pub_date": "TEXT",
    "pub_date_edition": "TEXT",
    "num_pages": "INTEGER",
    "isbn": "TEXT",
    "isbn13": "TEXT",
    "asin": "TEXT",
    "language": "TEXT",
    "genres": "TEXT",
    "json_details": "TEXT",
    "position": "INTEGER",
    "cover_url": "TEXT",
    "cover_local_path": "TEXT",
    "book_url": "TEXT",
    "avg_rating": "REAL",
    "num_ratings": "INTEGER",
    "rating": "INTEGER",
    "shelves": "TEXT",
    "review": "TEXT",
    "notes": "TEXT",
    "comments": "INTEGER",
    "votes": "INTEGER",
    "date_read": "TEXT",
    "date_started": "TEXT",
    "date_added": "TEXT",
    "date_purchased": "TEXT",
    "purchase_location": "TEXT",
    "owned": "TEXT",
    "condition": "TEXT",
    "format": "TEXT",
    "recommender": "TEXT",
    "read_count": "INTEGER",
    "cover_downloaded": "INTEGER",
    "last_synced": "TEXT",
    "fetched_at": "TEXT"
}


class Database:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = "./goodreads.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._ensure_tables_and_schema()

    def _ensure_tables_and_schema(self) -> None:
        try:
            cur = self.conn.cursor()

            # books
            columns_sql = ",\n  ".join(
                f"{col} {COLUMN_DEFS.get(col, 'TEXT')}" for col in BOOK_COLUMNS
            )
            cur.execute(f"CREATE TABLE IF NOT EXISTS books ({columns_sql});")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_books_date_added ON books(date_added);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);")

            # history
            cur.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                action TEXT,
                book_id TEXT,
                title TEXT,
                status TEXT,
                meta TEXT
            );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_history_ts ON history(ts);")

            # downloads
            cur.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goodreads_id TEXT,
                candidate_id TEXT,
                status TEXT,
                progress INTEGER,
                ts TEXT,
                meta TEXT
            );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_downloads_ts ON downloads(ts);")

            self.conn.commit()
            self._migrate_add_missing_columns()

        except Exception:
            LOG.exception("Failed to ensure DB schema")

    def _get_existing_columns(self) -> List[str]:
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(books);")
        rows = cur.fetchall()
        return [r["name"] for r in rows]

    def _migrate_add_missing_columns(self) -> None:
        try:
            existing = set(self._get_existing_columns())
            cur = self.conn.cursor()
            for col in BOOK_COLUMNS:
                if col not in existing:
                    col_def = COLUMN_DEFS.get(col, "TEXT")
                    LOG.info("Migrating DB: adding missing column %s %s", col, col_def)
                    cur.execute(f"ALTER TABLE books ADD COLUMN {col} {col_def};")
            self.conn.commit()
        except Exception:
            LOG.exception("DB migration failed while adding missing columns")

    # --- Book CRUD ---
    def save_book(self, book: Dict[str, Any], shelves: Optional[List[str]] = None) -> bool:
        """
        Save or update a book into DB.
        Ensures shelves normalization, JSON backup, and timestamps.
        """
        try:
            cur = self.conn.cursor()

            # fallback json snapshot
            json_details = book.get("json_details")
            if json_details is None:
                try:
                    json_details = json.dumps(book, ensure_ascii=False)
                except Exception:
                    json_details = json.dumps(str(book))

            # shelves normalization
            shelves_val = None
            if shelves:
                shelves_val = ", ".join(s.strip() for s in shelves if s and s.strip())
            elif book.get("shelves"):
                sv = book.get("shelves")
                shelves_val = ", ".join(sv) if isinstance(sv, list) else str(sv)

            # ensure timestamps
            now = datetime.utcnow().isoformat()
            book["last_synced"] = now
            if not book.get("fetched_at"):
                book["fetched_at"] = now

            values = []
            for col in BOOK_COLUMNS:
                if col == "json_details":
                    values.append(json_details)
                elif col == "shelves":
                    values.append(shelves_val if shelves_val is not None else book.get("shelves"))
                else:
                    values.append(book.get(col))

            placeholders = ",".join(["?"] * len(BOOK_COLUMNS))
            cols_sql = ",".join(BOOK_COLUMNS)
            sql = f"INSERT OR REPLACE INTO books ({cols_sql}) VALUES ({placeholders})"
            cur.execute(sql, values)
            self.conn.commit()
            return True
        except Exception:
            LOG.exception("Failed to save book to DB: %s", book.get("goodreads_id"))
            return False

    def get_book_by_id(self, goodreads_id: str) -> Optional[Dict[str, Any]]:
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM books WHERE goodreads_id = ?", (goodreads_id,))
            row = cur.fetchone()
            return dict(row) if row else None
        except Exception:
            LOG.exception("Failed to fetch book by id %s", goodreads_id)
            return None

    def update_book(self, goodreads_id: str, updates: Dict[str, Any]) -> bool:
        try:
            allowed = set(BOOK_COLUMNS)
            set_parts, params = [], []
            for k, v in updates.items():
                if k not in allowed:
                    continue
                if k == "json_details" and not isinstance(v, str):
                    v = json.dumps(v, ensure_ascii=False)
                set_parts.append(f"{k} = ?")
                params.append(v)
            if not set_parts:
                return False

            # always bump last_synced
            set_parts.append("last_synced = ?")
            params.append(datetime.utcnow().isoformat())

            params.append(goodreads_id)
            sql = f"UPDATE books SET {', '.join(set_parts)} WHERE goodreads_id = ?"
            self.conn.execute(sql, params)
            self.conn.commit()
            return True
        except Exception:
            LOG.exception("Failed to update book %s", goodreads_id)
            return False

    def delete_book(self, goodreads_id: str) -> bool:
        try:
            self.conn.execute("DELETE FROM books WHERE goodreads_id = ?", (goodreads_id,))
            self.conn.commit()
            return True
        except Exception:
            LOG.exception("Failed to delete book %s", goodreads_id)
            return False

    def get_books_by_shelf(self, shelf_name: str, limit: Optional[int] = 30, offset: Optional[int] = 0, order_by: str = "date_added DESC") -> List[Dict[str, Any]]:
        try:
            like_pattern = f"%{shelf_name}%"
            sql = f"SELECT * FROM books WHERE shelves LIKE ? ORDER BY {order_by}"
            params = [like_pattern]
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
                if offset:
                    sql += " OFFSET ?"
                    params.append(offset)
            cur = self.conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
        except Exception:
            LOG.exception("Failed to read books by shelf")
            return []

    def count_books_by_shelf(self, shelf: str) -> int:
        if not shelf:
            return 0
        try:
            row = self.conn.execute("SELECT COUNT(1) FROM books WHERE shelves LIKE ?", (f"%{shelf}%",)).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            LOG.exception("Failed to count books by shelf")
            return 0

    # --- Shelves ---
    def get_shelves(self) -> List[str]:
        try:
            cur = self.conn.execute("SELECT shelves FROM books WHERE shelves IS NOT NULL")
            sset = set()
            for r in cur.fetchall():
                parts = [p.strip() for p in str(r["shelves"]).split(",") if p.strip()]
                sset.update(parts)
            return sorted(sset)
        except Exception:
            LOG.exception("Failed to compute shelves")
            return []

    # --- History ---
    def add_history(self, action: str, book_id: Optional[str], title: Optional[str], status: str, meta: Optional[Dict[str, Any]] = None) -> bool:
        try:
            ts = datetime.utcnow().isoformat()
            meta_json = json.dumps(meta or {}, ensure_ascii=False)
            self.conn.execute(
                "INSERT INTO history (ts, action, book_id, title, status, meta) VALUES (?, ?, ?, ?, ?, ?)",
                (ts, action, book_id, title, status, meta_json),
            )
            self.conn.commit()
            return True
        except Exception:
            LOG.exception("Failed to add history entry for %s", book_id)
            return False

    def get_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        try:
            cur = self.conn.execute("SELECT * FROM history ORDER BY ts DESC LIMIT ?", (limit,))
            rows, out = cur.fetchall(), []
            for r in rows:
                rr = dict(r)
                try:
                    rr["meta"] = json.loads(rr.get("meta") or "{}")
                except Exception:
                    pass
                out.append(rr)
            return out
        except Exception:
            LOG.exception("Failed to get history")
            return []

    # --- Downloads ---
    def create_download(self, goodreads_id: str, candidate_id: str, status: str = "queued", meta: Optional[Dict[str, Any]] = None) -> bool:
        try:
            ts = datetime.utcnow().isoformat()
            self.conn.execute(
                "INSERT INTO downloads (goodreads_id, candidate_id, status, progress, ts, meta) VALUES (?, ?, ?, ?, ?, ?)",
                (goodreads_id, candidate_id, status, 0, ts, json.dumps(meta or {})),
            )
            self.conn.commit()
            return True
        except Exception:
            LOG.exception("Failed to create download entry")
            return False

    def update_download(self, candidate_id: str, status: Optional[str] = None, progress: Optional[int] = None, meta: Optional[Dict[str, Any]] = None) -> bool:
        try:
            set_parts, params = [], []
            if status:
                set_parts.append("status = ?")
                params.append(status)
            if progress is not None:
                set_parts.append("progress = ?")
                params.append(progress)
            if meta:
                set_parts.append("meta = ?")
                params.append(json.dumps(meta))
            if not set_parts:
                return False
            params.append(candidate_id)
            self.conn.execute(f"UPDATE downloads SET {', '.join(set_parts)} WHERE candidate_id = ?", params)
            self.conn.commit()
            return True
        except Exception:
            LOG.exception("Failed to update download %s", candidate_id)
            return False

    def mark_download_failed(self, candidate_id: str, error: str) -> bool:
        return self.update_download(candidate_id, status="failed", meta={"error": error})

    def get_active_downloads(self) -> List[Dict[str, Any]]:
        try:
            cur = self.conn.execute("SELECT * FROM downloads WHERE status IN ('queued','downloading') ORDER BY ts DESC")
            return [dict(r) for r in cur.fetchall()]
        except Exception:
            LOG.exception("Failed to fetch active downloads")
            return []

    def get_all_downloads(self, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            cur = self.conn.execute("SELECT * FROM downloads ORDER BY ts DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]
        except Exception:
            LOG.exception("Failed to fetch downloads")
            return []

    # --- Utils ---
    def vacuum(self) -> None:
        try:
            self.conn.execute("VACUUM;")
            self.conn.commit()
        except Exception:
            LOG.exception("VACUUM failed")

    def row_count(self) -> int:
        try:
            r = self.conn.execute("SELECT COUNT(1) FROM books").fetchone()
            return int(r[0]) if r else 0
        except Exception:
            LOG.exception("Failed to count rows")
            return 0

    def close(self) -> None:
        try:
            self.conn.commit()
            self.conn.close()
        except Exception:
            pass

    def get_books(
        self,
        search: Optional[str] = None,
        shelf: Optional[str] = None,
        order_by: str = "title COLLATE NOCASE ASC",
        limit: Optional[int] = 50,
        offset: Optional[int] = 0,
    ) -> List[Dict[str, Any]]:
        """
        Fetch books with optional search, shelf filter, ordering, and pagination.
        - search: free text applied to title/author/series_name
        - shelf: filter books containing this shelf name
        - order_by: SQL ORDER BY clause (default = title alphabetical)
        - limit/offset: pagination
        """
        try:
            clauses, params = [], []

            if search:
                like = f"%{search}%"
                clauses.append("(title LIKE ? OR author LIKE ? OR series_name LIKE ?)")
                params.extend([like, like, like])

            if shelf:
                clauses.append("shelves LIKE ?")
                params.append(f"%{shelf}%")

            where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

            sql = f"SELECT * FROM books {where_sql} ORDER BY {order_by}"
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
                if offset:
                    sql += " OFFSET ?"
                    params.append(offset)

            cur = self.conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
        except Exception:
            LOG.exception("Failed to fetch books with search=%s shelf=%s", search, shelf)
            return []


