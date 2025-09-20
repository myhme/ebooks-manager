import sqlite3
import json
from pathlib import Path

class Database:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS books (
                    goodreads_id TEXT PRIMARY KEY,
                    title TEXT,
                    author TEXT,
                    series_name TEXT,
                    series_number TEXT,
                    pub_date TEXT,
                    num_pages INTEGER,
                    isbn TEXT,
                    asin TEXT,
                    language TEXT,
                    genres TEXT,
                    json_details TEXT
                )
            ''')
            conn.commit()

    def save_book(self, details):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            json_details = json.dumps(details)
            cursor.execute('''
                INSERT OR REPLACE INTO books (goodreads_id, title, author, json_details)
                VALUES (?, ?, ?, ?)
            ''', (
                details['goodreads_id'],
                details.get('title'),
                details.get('author'),
                json_details
            ))
            conn.commit()
