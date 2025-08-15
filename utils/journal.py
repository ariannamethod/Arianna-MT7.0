import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
from typing import Dict

_JOURNAL_DIR = "data"
_JOURNAL_PATH = os.path.join(_JOURNAL_DIR, "journal.log")
_MAX_BYTES = 1_000_000

logger = logging.getLogger("journal")

if not logger.handlers:
    os.makedirs(_JOURNAL_DIR, exist_ok=True)
    handler = RotatingFileHandler(
        _JOURNAL_PATH, maxBytes=_MAX_BYTES, backupCount=3, encoding="utf-8"
    )
    formatter = logging.Formatter("%(asctime)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

_EMAIL_RE = re.compile(r"\b[\w\.-]+@[\w\.-]+\.\w+\b")
_DIGIT_RE = re.compile(r"\d")

def _mask(value: str) -> str:
    value = _EMAIL_RE.sub("[EMAIL]", value)
    value = _DIGIT_RE.sub("X", value)
    return value

def _sanitize(event: Dict) -> Dict:
    return {k: _mask(v) if isinstance(v, str) else v for k, v in event.items()}

def log_event(event: Dict) -> None:
    """Write an event dictionary to the rotating log file."""
    safe_event = _sanitize(event)
    try:
        logger.info(json.dumps(safe_event, ensure_ascii=False))
    except Exception as e:
        logger.error("Failed to write journal", exc_info=e)


def search_journal(query: str, limit: int = 20) -> list[str]:
    """Search the journal log for lines containing the query."""
    if not os.path.exists(_JOURNAL_PATH):
        return []
    query_l = query.lower()
    matches: list[str] = []
    try:
        with open(_JOURNAL_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if query_l in line.lower():
                    matches.append(line.strip())
                    if len(matches) >= limit:
                        break
    except Exception:
        return []
    return matches
