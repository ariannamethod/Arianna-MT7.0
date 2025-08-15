import asyncio
import importlib
import httpx

from utils.thread_store import load_threads, save_threads
import utils.thread_store as ts
import utils.thread_db as td
import utils.arianna_engine as ae


def test_menu_handler_uses_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:ABC")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    db_path = tmp_path / "threads.db"
    monkeypatch.setattr(ts, "THREAD_DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(td, "THREAD_DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(ae, "save_threads", lambda threads: save_threads(threads, path=str(db_path)), raising=False)
    monkeypatch.setattr(ae, "load_threads", lambda: load_threads(path=str(db_path)), raising=False)

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

    async def handler(request):
        return httpx.Response(200, json={"id": "thread-1"})
    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: orig_client(transport=transport))

    sa.engine.threads = {}
    async def fake_ask(thread_key, prompt, is_group):
        tid = await sa.engine._get_thread(thread_key)
        return f"resp:{tid}"
    monkeypatch.setattr(sa.engine, "ask", fake_ask)

    class Chat:
        id = 10
        type = "private"
    class FromUser:
        id = 42
    class Message:
        chat = Chat()
        from_user = FromUser()
        text = "hello?"
        message_id = 1
        entities = None
        reply_to_message = None
        responses = []
        async def answer(self, text, reply_markup=None):
            self.responses.append(text)
    m = Message()

    async def run():
        await sa.all_messages(m)
        await asyncio.gather(*tasks)
    asyncio.run(run())

    assert sa.engine.threads == {"42": "thread-1"}
    threads = load_threads(path=str(db_path))
    assert threads["42"] == "thread-1"
    assert responses == ["resp:thread-1"]
