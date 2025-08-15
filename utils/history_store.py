import os
import sqlite3
import threading
from typing import List, Dict

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

def get_context(chat_id: int, message_id: int, window: int = 10) -> List[Dict]:
    """Return ~window messages before/after given message_id."""
    with _lock:
        cur = _conn.execute(
            """
            SELECT message_id, direction, user_id, username, text
            FROM messages
            WHERE chat_id = ? AND message_id BETWEEN ? AND ?
            ORDER BY message_id
            """,
            (chat_id, message_id - window, message_id + window),
        )
        rows = cur.fetchall()
    return [dict(row) for row in rows]
