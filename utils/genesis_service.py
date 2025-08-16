import asyncio
import hashlib
import os
import sqlite3
import threading
from typing import Optional

from utils.logging import get_logger

logger = get_logger(__name__)

_thread: Optional[threading.Thread] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_task: Optional[asyncio.Task] = None
_start_lock = threading.Lock()
_running = False

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


def _worker():
    global _loop, _task, _running
    from utils.genesis_tool import get_genesis_instance

    inst = get_genesis_instance()
    loop = asyncio.new_event_loop()
    _loop = loop
    asyncio.set_event_loop(loop)
    try:
        _task = loop.create_task(inst.run())
        loop.run_until_complete(_task)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Genesis service terminated")
    finally:
        loop.close()
        _loop = None
        _task = None
        _running = False


def start_genesis_service():
    """Start the Genesis scheduler in a background thread."""
    global _thread, _running
    if not _start_lock.acquire(blocking=False):
        return _thread
    try:
        if _thread and _thread.is_alive():
            return _thread
        if _thread and not _thread.is_alive():
            _thread.join()
            _thread = None
            _running = False
        if _running:
            return _thread
        _thread = threading.Thread(target=_worker, name="genesis-service", daemon=True)
        _thread.start()
        _running = True
        return _thread
    finally:
        _start_lock.release()


def stop_genesis_service() -> None:
    """Cancel the Genesis scheduler and wait for the thread to finish."""
    global _thread, _loop, _task, _running
    if _loop and _task:
        _loop.call_soon_threadsafe(_task.cancel)
    if _thread:
        _thread.join()
    _thread = None
    _loop = None
    _task = None
    _running = False
