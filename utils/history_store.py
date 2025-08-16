import os
import sqlite3
import threading
from datetime import datetime
from typing import List, Dict, Optional

HISTORY_DB_PATH = os.getenv("HISTORY_DB_PATH", "data/history.db")

# Initialize connection and table
os.makedirs(os.path.dirname(HISTORY_DB_PATH), exist_ok=True)
_conn = sqlite3.connect(HISTORY_DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
with _conn:
    _conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            user_id INTEGER,
            username TEXT,
            direction TEXT NOT NULL,
            text TEXT,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
_lock = threading.Lock()

def log_message(
    chat_id: int,
    message_id: int,
    user_id: int | None,
    username: str | None,
    text: str,
    direction: str,
) -> None:
    """Store a single message in the history database."""
    with _lock, _conn:
        _conn.execute(
            """
            INSERT INTO messages (chat_id, message_id, user_id, username, direction, text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chat_id, message_id, user_id, username, direction, text),
        )

def get_context(
    chat_id: int,
    message_id: int,
    window: int = 10,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> List[Dict]:
    """Return exactly ``window`` messages before and after ``message_id``.

    Parameters
    ----------
    chat_id: int
        Telegram chat identifier.
    message_id: int
        Central message to fetch context around.
    window: int
        Number of messages to retrieve on each side.
    start: datetime | None
        Optional lower time bound for returned messages.
    end: datetime | None
        Optional upper time bound for returned messages.

    Selection is based on message timestamp rather than IDs to avoid gaps
    caused by missing or sparse ``message_id`` values.
    """
    with _lock:
        cur = _conn.execute(
            "SELECT message_id, direction, user_id, username, text, ts "
            "FROM messages WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        )
        center = cur.fetchone()
        if not center:
            return []
        ts: datetime = center["ts"]

        before_query = (
            "SELECT message_id, direction, user_id, username, text "
            "FROM messages WHERE chat_id = ? AND ts < ?"
        )
        before_params: List[object] = [chat_id, ts]
        if start:
            before_query += " AND ts >= ?"
            before_params.append(start)
        before_query += " ORDER BY ts DESC LIMIT ?"
        before_params.append(window)
        before = _conn.execute(before_query, before_params).fetchall()

        after_query = (
            "SELECT message_id, direction, user_id, username, text "
            "FROM messages WHERE chat_id = ? AND ts > ?"
        )
        after_params: List[object] = [chat_id, ts]
        if end:
            after_query += " AND ts < ?"
            after_params.append(end)
        after_query += " ORDER BY ts ASC LIMIT ?"
        after_params.append(window)
        after = _conn.execute(after_query, after_params).fetchall()

    rows = list(reversed(before))
    rows.append(center)
    rows.extend(after)
    return [dict(row) for row in rows]
