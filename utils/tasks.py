import asyncio
import logging
from typing import Coroutine, Any, Set

logger = logging.getLogger(__name__)

# Keep a reference to background tasks if tracking is enabled.
_tracked: Set[asyncio.Task] = set()

def create_task(coro: Coroutine[Any, Any, Any], *, name: str | None = None, track: bool = False) -> asyncio.Task:
    """Create an asyncio task that logs any exception.

    Parameters
    ----------
    coro: Coroutine[Any, Any, Any]
        The coroutine to run in the background.
    name: str | None, optional
        Optional task name (Python 3.8+).
    track: bool, default False
        If True, keep a reference to the task for later cancellation.

    Returns
    -------
    asyncio.Task
        The created task.
    """
    task = asyncio.create_task(coro, name=name) if name is not None else asyncio.create_task(coro)
    if track:
        _tracked.add(task)

    def _done_callback(t: asyncio.Task) -> None:
        try:
            t.result()
        except Exception:
            logger.exception("Background task %s failed", name or getattr(t, 'get_name', lambda: None)())
        finally:
            if track:
                _tracked.discard(t)

    task.add_done_callback(_done_callback)
    return task


async def cancel_tracked() -> None:
    """Cancel all tracked background tasks."""
    if not _tracked:
        return
    for task in list(_tracked):
        task.cancel()
    await asyncio.gather(*_tracked, return_exceptions=True)
    _tracked.clear()
