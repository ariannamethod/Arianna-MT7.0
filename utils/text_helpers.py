import difflib
import os
import asyncio
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup
import logging

ALLOWED_DOMAINS = {d for d in os.getenv("ALLOWED_DOMAINS", "").split(",") if d}
ALLOWED_CONTENT_TYPES = {"text/html", "text/plain"}
MAX_CONTENT_KB = int(os.getenv("MAX_CONTENT_KB", 100))
MAX_BYTES = MAX_CONTENT_KB * 1024

logger = logging.getLogger(__name__)

_session: aiohttp.ClientSession | None = None

async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session

def fuzzy_match(a, b):
    """Return similarity ratio between two strings."""
    return difflib.SequenceMatcher(None, a, b).ratio()

async def extract_text_from_url(url):
    """Fetches a web page asynchronously and returns visible text."""
    try:
        parsed = urlparse(url)
        if ALLOWED_DOMAINS and (parsed.hostname not in ALLOWED_DOMAINS):
            return "[Domain not allowed]"
        headers = {"User-Agent": "Mozilla/5.0 (Arianna Agent)"}
        session = await _get_session()
        try:
            async with session.get(url, timeout=10, headers=headers) as resp:
                resp.raise_for_status()
                ctype = resp.headers.get("Content-Type", "")
                if not any(allowed in ctype for allowed in ALLOWED_CONTENT_TYPES):
                    return f"[Unsupported content type: {ctype}]"
                content = await resp.content.read(MAX_BYTES)
                text = content.decode(resp.get_encoding() or "utf-8", errors="ignore")
        except asyncio.TimeoutError:
            logger.warning("Timeout fetching %s", url)
            return "[Timeout]"
        except Exception as e:
            logger.exception("Error fetching %s: %s", url, e)
            return f"[Error loading page: {e}]"
        soup = BeautifulSoup(text, "html.parser")
        for s in soup(["script", "style", "header", "footer", "nav", "aside"]):
            s.decompose()
        text = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)[:3500]
    except Exception as e:
        logger.exception("Unexpected error processing %s: %s", url, e)
        return f"[Error loading page: {e}]"
