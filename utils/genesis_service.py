import asyncio
import threading

from utils.genesis_tool import get_genesis_instance
from utils.logging import get_logger

logger = get_logger(__name__)


async def _run_inst(inst, stop_event: asyncio.Event):
    while not stop_event.is_set():
        try:
            await inst.run()
        except Exception:
            logger.exception("Genesis service terminated")
            await asyncio.sleep(5)


def _worker(stop_event: asyncio.Event):
    inst = get_genesis_instance()
    asyncio.run(_run_inst(inst, stop_event))


def start_genesis_service():
    """Start the Genesis scheduler in a background thread."""
    stop_event = asyncio.Event()
    thread = threading.Thread(
        target=_worker, args=(stop_event,), name="genesis-service", daemon=True
    )
    thread.start()
    return thread, stop_event


def stop_genesis_service(thread: threading.Thread, stop_event: asyncio.Event):
    """Stop the Genesis scheduler and wait for the background thread to finish."""
    stop_event.set()
    thread.join()
