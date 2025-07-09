import difflib
import aiohttp
from bs4 import BeautifulSoup

def fuzzy_match(a, b):
    """Return similarity ratio between two strings."""
    return difflib.SequenceMatcher(None, a, b).ratio()

async def extract_text_from_url(url):
    """Fetches a web page asynchronously and returns visible text."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Arianna Agent)"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10, headers=headers) as resp:
                resp.raise_for_status()
                text = await resp.text()
        soup = BeautifulSoup(text, "html.parser")
        for s in soup(["script", "style", "header", "footer", "nav", "aside"]):
            s.decompose()
        text = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)[:3500]
    except Exception as e:
        return f"[Error loading page: {e}]"
