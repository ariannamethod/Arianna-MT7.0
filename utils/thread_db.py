import os
import json
import sqlite3
from typing import Any, Dict, Optional

THREAD_DB_PATH = "data/threads.db"
THREAD_JSON_PATH = "data/threads.json"


def get_conn(path: str = THREAD_DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Ensure required tables exist."""
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS threads (
                key TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS context (
                key TEXT PRIMARY KEY,
                content TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                data TEXT
            )
            """
        )


def migrate_from_json(conn: sqlite3.Connection, json_path: str = THREAD_JSON_PATH) -> None:
    """Migrate legacy JSON thread mappings into SQLite."""
    if not os.path.isfile(json_path):
        return
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        with conn:
            for key, thread_id in data.items():
                conn.execute(
                    "INSERT OR REPLACE INTO threads (key, thread_id) VALUES (?, ?)",
                    (key, thread_id),
                )
        os.remove(json_path)
    except Exception:
        pass


def load_thread_map(conn: sqlite3.Connection) -> Dict[str, str]:
    cur = conn.execute("SELECT key, thread_id FROM threads")
    return {row["key"]: row["thread_id"] for row in cur.fetchall()}


def save_thread_map(conn: sqlite3.Connection, threads: Dict[str, str]) -> None:
    with conn:
        for key, thread_id in threads.items():
            conn.execute(
                "INSERT OR REPLACE INTO threads (key, thread_id) VALUES (?, ?)",
                (key, thread_id),
            )


def get_context(conn: sqlite3.Connection, key: str) -> Optional[str]:
    cur = conn.execute("SELECT content FROM context WHERE key = ?", (key,))
    row = cur.fetchone()
    return row["content"] if row else None


def save_context(conn: sqlite3.Connection, key: str, content: str) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO context (key, content) VALUES (?, ?)",
            (key, content),
        )


def get_metadata(conn: sqlite3.Connection, key: str) -> Optional[Dict[str, Any]]:
    cur = conn.execute("SELECT data FROM metadata WHERE key = ?", (key,))
    row = cur.fetchone()
    if not row or row["data"] is None:
        return None
    try:
        return json.loads(row["data"])
    except Exception:
        return None


def save_metadata(conn: sqlite3.Connection, key: str, data: Dict[str, Any]) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, data) VALUES (?, ?)",
            (key, json.dumps(data)),
        )
