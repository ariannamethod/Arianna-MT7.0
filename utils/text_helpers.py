import difflib
import os
import asyncio
import re
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup

try:  # optional but preferred for better extraction
    import trafilatura
except Exception:  # pragma: no cover - optional dependency
    trafilatura = None

from utils.logging import get_logger

ALLOWED_DOMAINS = {d for d in os.getenv("ALLOWED_DOMAINS", "").split(",") if d}
ALLOWED_CONTENT_TYPES = {"text/html", "text/plain"}
MAX_CONTENT_KB = int(os.getenv("MAX_CONTENT_KB", 100))
MAX_BYTES = MAX_CONTENT_KB * 1024

logger = get_logger(__name__)

_session: aiohttp.ClientSession | None = None


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session

def fuzzy_match(a, b):
    """Return similarity ratio between two strings."""
    return difflib.SequenceMatcher(None, a, b).ratio()


def _extract_links(text: str, allowed_domains: set[str] | None = None, keywords: set[str] | None = None) -> list[str]:
    """Return list of http(s) links found in ``text`` filtered by domains and keywords."""
    allowed_domains = {d.lower() for d in (allowed_domains or [])}
    keywords = {k.lower() for k in (keywords or [])}

    soup = BeautifulSoup(text, "html.parser")
    links = [a.get("href") for a in soup.find_all("a", href=True)]
    if not links:
        links = re.findall(r"https?://\S+", text)

    seen = set()
    valid: list[str] = []
    for link in links:
        if not isinstance(link, str):
            continue
        parsed = urlparse(link)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        domain = parsed.hostname.lower() if parsed.hostname else ""
        if allowed_domains and domain not in allowed_domains:
            continue
        if keywords and not any(k in link.lower() for k in keywords):
            continue
        if link not in seen:
            valid.append(link)
            seen.add(link)
    return valid

async def extract_text_from_url(url, session: aiohttp.ClientSession | None = None):
    """Fetch a web page asynchronously and return visible text."""
    try:
        parsed = urlparse(url)
        if ALLOWED_DOMAINS and (parsed.hostname not in ALLOWED_DOMAINS):
            return "[Domain not allowed]"
        headers = {"User-Agent": "Mozilla/5.0 (Arianna Agent)"}
        session = session or await _get_session()
        try:
            async with session.get(url, timeout=10, headers=headers) as resp:
                resp.raise_for_status()
                ctype = resp.headers.get("Content-Type", "")
                if not any(allowed in ctype for allowed in ALLOWED_CONTENT_TYPES):
                    return f"[Unsupported content type: {ctype}]"
                content = await resp.content.read(MAX_BYTES)
                html = content.decode(resp.get_encoding() or "utf-8", errors="ignore")
        except asyncio.TimeoutError:
            logger.warning("Timeout fetching %s", url)
            return "[Timeout]"
        except Exception as e:
            logger.exception("Error fetching %s: %s", url, e)
            return f"[Error loading page: {e}]"

        if trafilatura is not None:
            extracted = trafilatura.extract(html)
            if extracted:
                text = extracted
            else:
                soup = BeautifulSoup(html, "html.parser")
                for s in soup(["script", "style", "header", "footer", "nav", "aside"]):
                    s.decompose()
                text = soup.get_text(separator="\n")
        else:
            soup = BeautifulSoup(html, "html.parser")
            for s in soup(["script", "style", "header", "footer", "nav", "aside"]):
                s.decompose()
            text = soup.get_text(separator="\n")

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)[:3500]
    except Exception as e:
        logger.exception("Unexpected error processing %s: %s", url, e)
        return f"[Error loading page: {e}]"
