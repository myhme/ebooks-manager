#!/usr/bin/env python3
# src/utils/migrate_db.py
import sqlite3
import json
import sys
from pathlib import Path

def load_config(p: str = "/app/config/config.json"):
    """
    Load configuration JSON if available.
    """
    p = Path(p)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[migrate_db] Failed to parse config: {e}")
        return {}

def migrate_database(db_path: Path):
    """
    Apply schema migrations: ensure all desired columns exist.
    """
    if not db_path.exists():
        print("[migrate_db] DB not found at", db_path, "- nothing to migrate.")
        return

    print("[migrate_db] Migrating DB at:", db_path)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Fetch existing schema
    cur.execute("PRAGMA table_info(books)")
    cols = cur.fetchall()
    existing = {c[1] for c in cols}  # column name is index 1

    # Desired schema
    desired = {
        "title_raw": "TEXT",
        "title_clean": "TEXT",
        "author_first": "TEXT",
        "series_name": "TEXT",
        "series_number": "TEXT",
        "series_id": "TEXT",
        "position": "INTEGER",
        "cover_url": "TEXT",
        "cover_local_path": "TEXT",
        "book_url": "TEXT",
        "isbn": "TEXT",
        "isbn13": "TEXT",
        "asin": "TEXT",
        "avg_rating": "REAL",
        "num_ratings": "INTEGER",
        "date_pub": "TEXT",
        "date_pub_edition": "TEXT",
        "num_pages": "INTEGER",
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
        "owned": "INTEGER",
        "condition": "TEXT",
        "format": "TEXT",
        "recommender": "TEXT",
        "read_count": "INTEGER",
        "genres": "TEXT",
        "json_details": "TEXT",
        "last_seen": "TIMESTAMP"
    }

    # Apply migrations
    for col, typ in desired.items():
        if col not in existing:
            sql = f"ALTER TABLE books ADD COLUMN {col} {typ}"
            print("[migrate_db] Adding column:", col)
            try:
                cur.execute(sql)
            except Exception as e:
                print(f"[migrate_db] Failed to add {col}: {e}")
        else:
            print("[migrate_db] Already has column:", col)

    conn.commit()
    conn.close()
    print("[migrate_db] Migration complete.")

def main():
    cfg = load_config()
    db_path = Path(cfg.get("database_path", "/app/data/databases/goodreads.db"))
    migrate_database(db_path)

if __name__ == "__main__":
    try:
        main()
        sys.exit(0)
    except Exception as e:
        print("[migrate_db] Fatal error:", e)
        sys.exit(1)
