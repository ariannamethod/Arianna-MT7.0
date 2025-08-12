import asyncio

import httpx
import utils.arianna_engine as ae


class DummyResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data

    def raise_for_status(self):
        if not (200 <= self.status_code < 400):
            raise httpx.HTTPStatusError("error", request=None, response=None)


async def fake_post(self, url, headers=None, json=None, timeout=None):
    if "assistants" in url:
        return DummyResponse({"id": "assistant-xyz"})
    return DummyResponse({"id": "thread-123"})


def test_setup_assistant(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(ae, "genesis_tool_schema", lambda: {})
    engine = ae.AriannaEngine()
    monkeypatch.setattr(engine, "_load_system_prompt", lambda: "prompt")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post, raising=False)
    assistant_id = asyncio.run(engine.setup_assistant())
    assert assistant_id == "assistant-xyz"


def test_get_thread_creates_new(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(ae, "load_threads", lambda: {})
    recorded = {}
    monkeypatch.setattr(ae, "save_threads", lambda threads: recorded.update(threads))
    engine = ae.AriannaEngine()
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post, raising=False)
    tid = asyncio.run(engine._get_thread("user1"))
    assert tid == "thread-123"
    assert recorded["user1"] == "thread-123"
