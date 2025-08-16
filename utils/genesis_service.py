import asyncio
import hashlib
import os
import sqlite3
from utils.logging import get_logger

logger = get_logger(__name__)

_EVENT_DB_PATH = "data/genesis_events.db"


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_EVENT_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_EVENT_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            func TEXT PRIMARY KEY,
            timestamp REAL,
            payload_hash TEXT
        )
        """
    )
    return conn


def register_event(func: str, timestamp: float, payload: str) -> bool:
    """Register event execution and suppress duplicates.

    Returns ``True`` if event is new, ``False`` if duplicate was detected.
    """
    payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    with _get_conn() as conn:
        cur = conn.execute(
            "SELECT timestamp, payload_hash FROM events WHERE func = ?",
            (func,),
        )
        row = cur.fetchone()
        if row and (row[0] == timestamp or row[1] == payload_hash):
            return False
        conn.execute(
            "INSERT OR REPLACE INTO events (func, timestamp, payload_hash) VALUES (?, ?, ?)",
            (func, timestamp, payload_hash),
        )
    return True


async def run_genesis_service() -> None:
    """Run the Genesis scheduler inside the main event loop."""
    from utils.genesis_tool import get_genesis_instance

    inst = get_genesis_instance()
    try:
        await inst.run()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Genesis service terminated")
