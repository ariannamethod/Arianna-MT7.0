import json
import logging
import os
import time
from typing import List, Dict
from utils.vector_store import log_snippet

logger = logging.getLogger(__name__)

_JOURNAL_DIR = "data"
_JOURNAL_PATH = os.path.join(_JOURNAL_DIR, "journal.json")


def _load_journal() -> List[Dict]:
    """Load existing journal entries from the JSON file."""
    try:
        with open(_JOURNAL_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def log_event(event: Dict) -> None:
    """Append an event dictionary to ``data/journal.json``."""
    os.makedirs(_JOURNAL_DIR, exist_ok=True)
    entries = _load_journal()
    entries.append(event)
    try:
        with open(_JOURNAL_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Failed to write journal", exc_info=e)


async def log_event_pinecone(event: Dict, openai_api_key: str) -> None:
    """Store an event's text embedding in Pinecone."""
    text = event.get("reply") or event.get("prompt") or ""
    if not text.strip():
        return
    meta = {"type": "journal", "ts": event.get("timestamp", time.time())}
    try:
        await log_snippet(text, openai_api_key, meta)
    except Exception:
        logger.warning("Failed to log snippet to Pinecone", exc_info=True)
