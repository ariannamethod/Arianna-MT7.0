import os
import httpx

from utils.cache import async_ttl_cache
from utils.config import HTTP_TIMEOUT, CACHE_TTL

# You can add multiple keys for rotation if needed. If no key is provided,
# the list will be empty and DeepSeek calls are disabled.
deepseek_key = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_KEYS = [deepseek_key] if deepseek_key else []
DEEPSEEK_ENABLED = bool(deepseek_key)
current_key_idx = 0

def rotate_deepseek_key():
    """Rotate to the next DeepSeek API key (for use if multiple keys are provided)."""
    if not DEEPSEEK_KEYS:
        return None
    global current_key_idx
    current_key_idx = (current_key_idx + 1) % len(DEEPSEEK_KEYS)
    return DEEPSEEK_KEYS[current_key_idx]

@async_ttl_cache(ttl=CACHE_TTL)
async def call_deepseek(messages):
    """
    Send chat messages to DeepSeek API and return the response content.
    Automatically rotates key on 401 Unauthorized.
    """
    if not DEEPSEEK_KEYS:
        return "DeepSeek API key not configured"
    global current_key_idx
    key = DEEPSEEK_KEYS[current_key_idx]
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.45,
        "max_tokens": 700,
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(url, json=payload, headers=headers)
    try:
        data = resp.json()
    except Exception:
        data = {}
    if resp.status_code == 401:
        rotate_deepseek_key()
        return None
    if resp.status_code != 200:
        return None
    if "choices" in data and data["choices"]:
        reply = data["choices"][0]["message"]["content"].strip()
        if not reply:
            return None
        return reply
    return None
