import asyncio
import threading
from typing import Optional

from utils.logging import get_logger
from utils.state_snapshot import StateSnapshotter

logger = get_logger(__name__)

_thread: Optional[threading.Thread] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_task: Optional[asyncio.Task] = None


def _worker() -> None:
    global _loop, _task
    snapshotter = StateSnapshotter()
    loop = asyncio.new_event_loop()
    _loop = loop
    asyncio.set_event_loop(loop)
    try:
        _task = loop.create_task(snapshotter.run())
        loop.run_until_complete(_task)
    except asyncio.CancelledError:
        pass
    except Exception:  # pragma: no cover - logging only
        logger.exception("Snapshot service terminated")
    finally:
        loop.close()
        _loop = None
        _task = None


def start_snapshot_service() -> threading.Thread:
    """Start the state snapshot scheduler in a background thread."""
    global _thread
    if _thread and _thread.is_alive():
        return _thread
    _thread = threading.Thread(target=_worker, name="snapshot-service", daemon=True)
    _thread.start()
    return _thread


def stop_snapshot_service() -> None:
    """Cancel the snapshot scheduler and wait for it to finish."""
    global _thread, _loop, _task
    if _loop and _task:
        _loop.call_soon_threadsafe(_task.cancel)
    if _thread:
        _thread.join()
    _thread = None
    _loop = None
    _task = None
