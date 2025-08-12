import asyncio
import threading

from utils.genesis_tool import get_genesis_instance
from utils.logging import get_logger

logger = get_logger(__name__)

def _worker():
    inst = get_genesis_instance()
    try:
        asyncio.run(inst.run())
    except Exception:
        logger.exception("Genesis service terminated")

def start_genesis_service():
    """Start the Genesis scheduler in a background thread."""
    thread = threading.Thread(target=_worker, name="genesis-service", daemon=True)
    thread.start()
    return thread
