#!/usr/bin/env python3
# src/utils/migrate_db.py
import sqlite3, json, sys
from pathlib import Path

def load_config(p="/app/config/config.json"):
    p = Path(p)
    if not p.exists():
        return {}
    return json.loads(p.read_text())

cfg = load_config()
db_path = cfg.get("database_path", "/app/data/databases/goodreads.db")
db_path = Path(db_path)
if not db_path.exists():
    print("DB not found at", db_path, "- nothing to migrate.")
    sys.exit(0)

print("Migrating DB at:", db_path)
conn = sqlite3.connect(str(db_path))
cur = conn.cursor()
cur.execute("PRAGMA table_info(books)")
cols = cur.fetchall()
existing = {c[1] for c in cols}  # name is index 1

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

for col, typ in desired.items():
    if col not in existing:
        sql = f"ALTER TABLE books ADD COLUMN {col} {typ}"
        print("Adding column:", col)
        try:
            cur.execute(sql)
        except Exception as e:
            print("Failed to add", col, ":", e)
    else:
        print("Already has column:", col)

conn.commit()
conn.close()
print("Migration complete.")
