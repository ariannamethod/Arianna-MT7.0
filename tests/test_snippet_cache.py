import asyncio
import os
import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "123:ABC")
os.environ.setdefault("OPENAI_API_KEY", "test")

from server_arianna import append_link_snippets


class DummyRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

def test_append_link_snippets_uses_cache(monkeypatch):
    dummy = DummyRedis()
    monkeypatch.setattr("server_arianna._redis", dummy)

    calls = {"count": 0}

    async def fake_extract(url, session=None):
        calls["count"] += 1
        return "content"

    async def fake_session():
        return None

    monkeypatch.setattr("server_arianna.extract_text_from_url", fake_extract)
    monkeypatch.setattr("server_arianna._get_http_session", fake_session)

    text = "see https://example.com"  # link for extraction
    first = asyncio.run(append_link_snippets(text))
    second = asyncio.run(append_link_snippets(text))
    assert calls["count"] == 1
    assert "content" in first and "content" in second
