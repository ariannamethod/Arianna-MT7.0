import asyncio
import threading
from typing import Optional

from utils.genesis_tool import get_genesis_instance
from utils.logging import get_logger

logger = get_logger(__name__)

_thread: Optional[threading.Thread] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_task: Optional[asyncio.Task] = None
_start_lock = threading.Lock()
_running = False


def _worker():
    global _loop, _task, _running
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
    if _running:
        return _thread
    if not _start_lock.acquire(blocking=False):
        return _thread
    try:
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
