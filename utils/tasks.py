import asyncio
from typing import Coroutine, Any, Set
from time import perf_counter

from utils.logging import get_logger

logger = get_logger(__name__)

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

    start = perf_counter()

    def _done_callback(t: asyncio.Task) -> None:
        duration = perf_counter() - start
        try:
            t.result()
        except asyncio.CancelledError:
            logger.info(
                "Background task %s cancelled after %.2fs",
                name or getattr(t, "get_name", lambda: None)(),
                duration,
            )
        except Exception:
            logger.exception(
                "Background task %s failed after %.2fs",
                name or getattr(t, "get_name", lambda: None)(),
                duration,
            )
        else:
            logger.info(
                "Background task %s finished in %.2fs",
                name or getattr(t, "get_name", lambda: None)(),
                duration,
            )
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
