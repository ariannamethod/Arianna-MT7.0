import asyncio
import importlib

from datetime import datetime, timedelta
from utils.history_store import log_message


def test_reply_context(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:ABC")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    hist_path = tmp_path / "history.db"
    monkeypatch.setenv("HISTORY_DB_PATH", str(hist_path))
    mem_path = tmp_path / "memory.db"
    monkeypatch.setenv("MEMORY_DB_PATH", str(mem_path))

    sa = importlib.import_module("server_arianna")
    monkeypatch.setattr(sa, "re", __import__("re"), raising=False)

    class DummyCAS:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            pass
    monkeypatch.setattr(sa, "ChatActionSender", DummyCAS)

    async def ok_rate(user_id):
        return True
    monkeypatch.setattr(sa, "rate_limited", ok_rate, raising=False)

    async def identity(text):
        return text
    monkeypatch.setattr(sa, "append_link_snippets", identity, raising=False)

    sa.VOICE_ENABLED = {}
    monkeypatch.setattr(sa, "BOT_ID", 1, raising=False)
    monkeypatch.setattr(sa, "BOT_USERNAME", "arianna", raising=False)

    tasks = []
    def create_task_stub(coro, track=True):
        tasks.append(asyncio.create_task(coro))
    monkeypatch.setattr(sa, "create_task", create_task_stub, raising=False)

    responses = []
    async def fake_send_delayed(m, resp, is_group, thread_key):
        responses.append(resp)
    monkeypatch.setattr(sa, "send_delayed_response", fake_send_delayed, raising=False)

    async def fake_search(query):
        return ["VS-hit"]
    sa.get_vector_store()
    monkeypatch.setattr(sa.vector_store, "semantic_search", fake_search, raising=False)

    def fake_journal(query):
        return ["J-hit"]
    monkeypatch.setattr(sa, "search_journal", fake_journal, raising=False)

    def fake_semantic(text):
        return [{"ts": now, "content": "mem"}]
    monkeypatch.setattr(sa, "semantic_query", fake_semantic, raising=False)

    captured = {}
    async def fake_ask(thread_key, prompt, is_group):
        captured["prompt"] = prompt
        return "ok"
    monkeypatch.setattr(sa.engine, "ask", fake_ask, raising=False)

    chat_id = 10
    log_message(chat_id, 8, 42, "user", "old question", "in")
    import time
    time.sleep(1)
    log_message(chat_id, 9, 1, "arianna", "old answer", "out")

    now = datetime.utcnow()
    class Reply:
        message_id = 9
        date = now - timedelta(minutes=1)
        class From:
            id = 1
        from_user = From()
    class Chat:
        id = chat_id
        type = "private"
    class FromUser:
        id = 42
    class Message:
        chat = Chat()
        from_user = FromUser()
        text = "follow up?"
        message_id = 20
        date = now
        entities = None
        reply_to_message = Reply()
        responses = []
        async def answer(self, text, reply_markup=None):
            self.responses.append(text)
            return type("msg", (), {"message_id": 0})
    m = Message()

    async def run():
        await sa.all_messages(m)
        await asyncio.gather(*tasks)
    asyncio.run(run())

    prompt = captured["prompt"]
    assert "old question" in prompt
    assert "old answer" in prompt
    assert "VS-hit" in prompt
    assert "J-hit" in prompt
    assert "mem" in prompt
    assert "follow up?" in prompt
