import os
import sqlite3
import threading
from array import array
from datetime import datetime
from math import sqrt
from typing import List, Optional, Dict, Any

try:  # pragma: no cover - network dependency
    from openai import OpenAI  # type: ignore
    _emb_client = OpenAI()
except Exception:  # pragma: no cover - missing dependency or key
    _emb_client = None

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
    _conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_vectors (
            id INTEGER PRIMARY KEY,
            embedding BLOB
        )
        """
    )
_lock = threading.Lock()


def add_event(event_type: str, content: str, tags: Optional[List[str]] = None, ts: Optional[datetime] = None) -> None:
    """Store a generic event in the memory database."""
    tag_str = ",".join(tags) if tags else None
    with _lock, _conn:
        cur = _conn.execute(
            "INSERT INTO events (ts, type, content, tags) VALUES (?, ?, ?, ?)",
            (ts or datetime.utcnow(), event_type, content, tag_str),
        )
        event_id = cur.lastrowid
        emb = _embed_text(content)
        if emb:
            arr = array("f", emb)
            _conn.execute(
                "INSERT OR REPLACE INTO memory_vectors (id, embedding) VALUES (?, ?)",
                (event_id, arr.tobytes()),
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


def _embed_text(text: str) -> List[float]:  # pragma: no cover - heavy network
    if _emb_client is None:
        return []
    try:
        res = _emb_client.embeddings.create(
            model="text-embedding-ada-002",
            input=text,
        )
        return res.data[0].embedding  # type: ignore[attr-defined]
    except Exception:
        return []


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sqrt(sum(x * x for x in a))
    norm_b = sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def semantic_query(text: str, limit: int = 5) -> List[Dict[str, Any]]:
    emb = _embed_text(text)
    if not emb:
        return []
    with _lock:
        cur = _conn.execute(
            "SELECT e.id, e.ts, e.type, e.content, e.tags, v.embedding FROM events e "
            "JOIN memory_vectors v ON e.id = v.id"
        )
        rows = cur.fetchall()
    scored: List[tuple[float, Dict[str, Any]]] = []
    for row in rows:
        arr = array("f")
        arr.frombytes(row["embedding"])
        score = _cosine(emb, arr.tolist())
        scored.append((score, dict(row)))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:limit]]
