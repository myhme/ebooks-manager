# src/utils/database.py
"""
Robust SQLite database wrapper for ebooks-manager.

- Creates/maintains a `books` table with a wide column set to accept scraper data.
- Creates a `history` table for sync/fetch events.
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
    "series_name", "series_number", "series_id", "pub_date", "pub_date_edition",
    "num_pages", "isbn", "isbn13", "asin", "language",
    "genres", "json_details", "position", "cover_url", "cover_local_path",
    "book_url", "avg_rating", "num_ratings", "rating", "shelves",
    "review", "notes", "comments", "votes", "date_read",
    "date_started", "date_added", "date_purchased", "purchase_location", "owned",
    "condition", "format", "recommender", "read_count", "cover_downloaded",
    "last_synced"
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
    "last_synced": "TEXT"
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

            columns_sql = ",\n  ".join(
                f"{col} {COLUMN_DEFS.get(col, 'TEXT')}" for col in BOOK_COLUMNS
            )
            create_books_sql = f"""
            CREATE TABLE IF NOT EXISTS books (
              {columns_sql}
            );
            """
            cur.execute(create_books_sql)

            cur.execute("CREATE INDEX IF NOT EXISTS idx_books_date_added ON books(date_added);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);")

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

    def close(self) -> None:
        try:
            self.conn.commit()
            self.conn.close()
        except Exception:
            pass

    def save_book(self, book: Dict[str, Any], shelves: Optional[List[str]] = None) -> bool:
        try:
            cur = self.conn.cursor()

            json_details = book.get("json_details")
            if json_details is None:
                try:
                    json_details = json.dumps(book, ensure_ascii=False)
                except Exception:
                    json_details = json.dumps(str(book))

            shelves_val = None
            if shelves:
                shelves_val = ", ".join(s.strip() for s in shelves if s and s.strip())
            elif book.get("shelves"):
                sv = book.get("shelves")
                if isinstance(sv, list):
                    shelves_val = ", ".join(sv)
                else:
                    shelves_val = str(sv)

            values = []
            for col in BOOK_COLUMNS:
                if col == "json_details":
                    values.append(json_details)
                    continue
                if col == "shelves":
                    values.append(shelves_val if shelves_val is not None else book.get("shelves"))
                    continue
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
            if not row:
                return None
            return dict(row)
        except Exception:
            LOG.exception("Failed to fetch book by id %s", goodreads_id)
            return None

    def update_book(self, goodreads_id: str, updates: Dict[str, Any]) -> bool:
        try:
            allowed = set(BOOK_COLUMNS)
            set_parts = []
            params = []
            for k, v in updates.items():
                if k not in allowed:
                    LOG.debug("Ignoring unknown update column: %s", k)
                    continue
                set_parts.append(f"{k} = ?")
                if k == "json_details" and not isinstance(v, str):
                    params.append(json.dumps(v, ensure_ascii=False))
                else:
                    params.append(v)
            if not set_parts:
                return False
            params.append(goodreads_id)
            sql = f"UPDATE books SET {', '.join(set_parts)} WHERE goodreads_id = ?"
            cur = self.conn.cursor()
            cur.execute(sql, params)
            self.conn.commit()
            return True
        except Exception:
            LOG.exception("Failed to update book %s", goodreads_id)
            return False

    def delete_book(self, goodreads_id: str) -> bool:
        try:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM books WHERE goodreads_id = ?", (goodreads_id,))
            self.conn.commit()
            return True
        except Exception:
            LOG.exception("Failed to delete book %s", goodreads_id)
            return False

    def get_books_by_shelf(
        self,
        shelf_name: str,
        limit: Optional[int] = 30,
        offset: Optional[int] = 0,
        order_by: str = "date_added DESC"
    ) -> List[Dict[str, Any]]:
        try:
            cur = self.conn.cursor()
            like_pattern = f"%{shelf_name}%"
            sql = f"SELECT * FROM books WHERE shelves LIKE ? ORDER BY {order_by}"
            params = [like_pattern]
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
                if offset:
                    sql += " OFFSET ?"
                    params.append(offset)
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        except Exception:
            LOG.exception("Failed to read books by shelf")
            return []

    def get_all_books(self, limit: Optional[int] = None, offset: Optional[int] = 0) -> List[Dict[str, Any]]:
        try:
            cur = self.conn.cursor()
            sql = "SELECT * FROM books ORDER BY date_added DESC"
            params = []
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
                if offset:
                    sql += " OFFSET ?"
                    params.append(offset)
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        except Exception:
            LOG.exception("Failed to fetch all books")
            return []

    def get_shelves(self) -> List[str]:
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT shelves FROM books WHERE shelves IS NOT NULL")
            rows = cur.fetchall()
            sset = set()
            for r in rows:
                s = r["shelves"]
                if not s:
                    continue
                parts = [p.strip() for p in str(s).split(",")]
                for p in parts:
                    if p:
                        sset.add(p)
            return sorted(sset)
        except Exception:
            LOG.exception("Failed to compute shelves")
            return []

    def add_history(self, action: str, book_id: Optional[str], title: Optional[str], status: str, meta: Optional[Dict[str, Any]] = None) -> bool:
        try:
            cur = self.conn.cursor()
            ts = datetime.utcnow().isoformat()
            meta_json = json.dumps(meta or {}, ensure_ascii=False)
            cur.execute("INSERT INTO history (ts, action, book_id, title, status, meta) VALUES (?, ?, ?, ?, ?, ?)",
                        (ts, action, book_id, title, status, meta_json))
            self.conn.commit()
            return True
        except Exception:
            LOG.exception("Failed to add history entry for %s", book_id)
            return False

    def get_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM history ORDER BY ts DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
            out = []
            for r in rows:
                rr = dict(r)
                try:
                    rr["meta"] = json.loads(rr.get("meta") or "{}")
                except Exception:
                    rr["meta"] = rr.get("meta")
                out.append(rr)
            return out
        except Exception:
            LOG.exception("Failed to get history")
            return []

    def vacuum(self) -> None:
        try:
            cur = self.conn.cursor()
            cur.execute("VACUUM;")
            self.conn.commit()
        except Exception:
            LOG.exception("VACUUM failed")

    def row_count(self) -> int:
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT COUNT(1) AS c FROM books")
            r = cur.fetchone()
            return int(r["c"]) if r else 0
        except Exception:
            LOG.exception("Failed to count rows")
            return 0
