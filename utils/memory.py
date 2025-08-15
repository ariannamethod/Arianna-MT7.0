import os
import sqlite3
import threading
from datetime import datetime
from typing import List, Optional, Dict, Any

MEMORY_DB_PATH = os.getenv("MEMORY_DB_PATH", "data/memory.db")

os.makedirs(os.path.dirname(MEMORY_DB_PATH), exist_ok=True)
_conn = sqlite3.connect(MEMORY_DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
with _conn:
    _conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP,
            type TEXT NOT NULL,
            content TEXT,
            tags TEXT
        )
        """
    )
_lock = threading.Lock()


def add_event(event_type: str, content: str, tags: Optional[List[str]] = None, ts: Optional[datetime] = None) -> None:
    """Store a generic event in the memory database."""
    tag_str = ",".join(tags) if tags else None
    with _lock, _conn:
        _conn.execute(
            "INSERT INTO events (ts, type, content, tags) VALUES (?, ?, ?, ?)",
            (ts or datetime.utcnow(), event_type, content, tag_str),
        )


def query_events(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    tags: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Retrieve events filtered by optional time range and tags."""
    query = "SELECT ts, type, content, tags FROM events WHERE 1=1"
    params: List[Any] = []
    if start:
        query += " AND ts >= ?"
        params.append(start)
    if end:
        query += " AND ts <= ?"
        params.append(end)
    if tags:
        query += " AND (" + " OR ".join(["tags LIKE ?"] * len(tags)) + ")"
        params.extend([f"%{t}%" for t in tags])
    query += " ORDER BY ts DESC"
    with _lock:
        cur = _conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]
