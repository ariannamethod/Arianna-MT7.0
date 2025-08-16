import asyncio
from utils.logging import get_logger
from utils.state_snapshot import StateSnapshotter

logger = get_logger(__name__)


async def run_snapshot_service() -> None:
    """Run the state snapshot scheduler inside the main event loop."""
    snapshotter = StateSnapshotter()
    try:
        await snapshotter.run()
    except asyncio.CancelledError:
        raise
    except Exception:  # pragma: no cover - logging only
        logger.exception("Snapshot service terminated")
